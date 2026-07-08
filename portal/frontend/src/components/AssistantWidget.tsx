"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  chatWithModels,
  fetchInsights,
  listChatModels,
  ChatProvider,
  ChatToolCall,
  Insights,
  JobResponse,
} from "@/lib/api";

type Props = {
  token: string;
  jobs?: JobResponse[];
  initialJobId?: string | null;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ChatToolCall[];
};

type Attachment = { name: string; base64: string; size: number };

type Conversation = {
  id: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: number;
};

const PROVIDERS: { value: ChatProvider; label: string }[] = [
  { value: "exaone", label: "로컬 EXAONE (키 불필요)" },
  { value: "anthropic", label: "Claude" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Gemini" },
];

// EXAONE is self-hosted behind the SSO gate — it needs no API key.
const DEFAULT_PROVIDER: ChatProvider = "exaone";
const providerNeedsKey = (provider: ChatProvider) => provider !== "exaone";

const MAX_TOTAL_BYTES = 40 * 1024 * 1024; // 40 MB total across attachments
const CONV_KEY = "bmp_chat_conversations";
const MAX_CONVERSATIONS = 30;

const providerKeyStore = (provider: ChatProvider) => `bmp_chat_key_${provider}`;
const providerModelStore = (provider: ChatProvider) => `bmp_chat_model_${provider}`;

const loadConversations = (): Conversation[] => {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(CONV_KEY);
    const parsed = raw ? (JSON.parse(raw) as Conversation[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const persistConversations = (convs: Conversation[]) => {
  if (typeof window === "undefined") return;
  const trimmed = [...convs].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS);
  window.localStorage.setItem(CONV_KEY, JSON.stringify(trimmed));
};

const newId = () =>
  typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Date.now());

function toolChip(call: ChatToolCall): string {
  const ok = call.result?.ok !== false;
  const jobId = (call.result?.job_id as string | undefined) ?? undefined;
  const detail = jobId ? ` · ${jobId.slice(0, 8)}…` : "";
  return `🔧 ${call.name}${detail} ${ok ? "✓" : "✕"}`;
}

// Render assistant markdown compactly inside the narrow chat panel: GFM tables,
// lists, code, and links, scoped so it doesn't inherit the app's prose styles.
function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="markdown-chat space-y-2 text-slate-600 [&_a]:text-brand-600 [&_a]:underline [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[10px] [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-4 [&_li]:list-disc [&_ol_li]:list-decimal [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-slate-900 [&_pre]:p-2 [&_pre]:text-slate-100 [&_pre_code]:bg-transparent [&_pre_code]:text-slate-100 [&_strong]:font-semibold [&_table]:my-1 [&_table]:block [&_table]:overflow-x-auto [&_td]:border [&_td]:border-slate-200 [&_td]:px-1.5 [&_td]:py-0.5 [&_th]:border [&_th]:border-slate-200 [&_th]:bg-slate-50 [&_th]:px-1.5 [&_th]:py-0.5">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

const fileToBase64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(((reader.result as string) || "").split(",")[1] || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });

export function AssistantWidget({ token, jobs, initialJobId }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);

  const [provider, setProvider] = useState<ChatProvider>(DEFAULT_PROVIDER);
  const [apiKey, setApiKey] = useState("");
  const apiKeyRef = useRef("");
  const [model, setModel] = useState(""); // "" = provider default
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [contextJobId, setContextJobId] = useState<string | null>(initialJobId ?? null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [reading, setReading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Conversation persistence (browser-only; never sent to our DB).
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showConvList, setShowConvList] = useState(false);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);

  // Learned guidance (read-only, aggregate; visible to all users).
  const [insights, setInsights] = useState<Insights | null>(null);
  const [insightsOpen, setInsightsOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  // Load persisted provider + key + model + conversations from the browser.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = (window.localStorage.getItem("bmp_chat_provider") as ChatProvider) || DEFAULT_PROVIDER;
    setProvider(saved);
    const key = window.localStorage.getItem(providerKeyStore(saved)) || "";
    setApiKey(key);
    apiKeyRef.current = key;
    setModel(window.localStorage.getItem(providerModelStore(saved)) || "");
    setConversations(loadConversations());
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("bmp_chat_provider", provider);
    const key = window.localStorage.getItem(providerKeyStore(provider)) || "";
    setApiKey(key);
    apiKeyRef.current = key;
    setModel(window.localStorage.getItem(providerModelStore(provider)) || "");
    setModels([]); // provider changed — clear the stale list
  }, [provider]);

  const loadModels = async () => {
    const key = apiKeyRef.current.trim();
    if (providerNeedsKey(provider) && !key) {
      setError("모델을 불러오려면 먼저 API 키를 입력하세요.");
      return;
    }
    setModelsLoading(true);
    setError(null);
    try {
      const res = await listChatModels({ provider, api_key: key }, token);
      setModels(res.models || []);
    } catch (err: any) {
      setError(err.message || "모델 목록을 불러오지 못했습니다.");
    } finally {
      setModelsLoading(false);
    }
  };

  // Auto-load models when the widget opens or the provider changes, if a key is
  // already present. (Not keyed on apiKey, to avoid refetching on every keystroke.)
  useEffect(() => {
    if (
      isOpen &&
      (!providerNeedsKey(provider) || apiKeyRef.current.trim()) &&
      models.length === 0 &&
      !modelsLoading
    ) {
      void loadModels();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, provider]);

  const handleModelChange = (value: string) => {
    setModel(value);
    if (typeof window !== "undefined") window.localStorage.setItem(providerModelStore(provider), value);
  };

  useEffect(() => {
    if (initialJobId) setContextJobId(initialJobId);
  }, [initialJobId]);

  // Keep the newest message in view as the conversation grows / while loading.
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  // Load learned guidance once the widget opens (best-effort; ignore failures).
  useEffect(() => {
    if (!isOpen || insights !== null) return;
    fetchInsights(token)
      .then(setInsights)
      .catch(() => setInsights({ active: false, payload: { recommended_defaults: {}, warnings: [], recipes: [] }, summary: null, updated_at: null }));
  }, [isOpen, insights, token]);

  const handleKeyChange = (value: string) => {
    setApiKey(value);
    apiKeyRef.current = value;
    if (typeof window !== "undefined") window.localStorage.setItem(providerKeyStore(provider), value);
  };

  const contextJob = useMemo(() => jobs?.find((j) => j.id === contextJobId), [jobs, contextJobId]);

  // --- Conversation management ---------------------------------------------

  const upsertConversation = (id: string, msgs: ChatMessage[]) => {
    const firstUser = msgs.find((m) => m.role === "user");
    const title = (firstUser?.content || "새 대화").slice(0, 40);
    setConversations((prev) => {
      const others = prev.filter((c) => c.id !== id);
      const next = [{ id, title, messages: msgs, updatedAt: Date.now() }, ...others];
      persistConversations(next);
      return next;
    });
  };

  const startNewChat = () => {
    setActiveConvId(null);
    setMessages([]);
    setAttachments([]);
    setError(null);
  };

  const loadConversation = (id: string) => {
    const conv = conversations.find((c) => c.id === id);
    if (!conv) return;
    setActiveConvId(id);
    setMessages(conv.messages);
    setAttachments([]);
    setError(null);
  };

  const deleteConversation = (id: string) => {
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      persistConversations(next);
      return next;
    });
    if (activeConvId === id) startNewChat();
  };

  const clearAllConversations = () => {
    if (typeof window !== "undefined" && !window.confirm("모든 대화를 삭제할까요? 되돌릴 수 없습니다.")) {
      return;
    }
    setConversations([]);
    persistConversations([]);
    setShowConvList(false);
    startNewChat();
  };

  // --- Attachments ----------------------------------------------------------

  const addFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setError(null);
    setReading(true);
    try {
      const incoming: Attachment[] = [];
      for (const file of Array.from(fileList)) {
        const name = (file as any).webkitRelativePath || file.name;
        try {
          incoming.push({ name, base64: await fileToBase64(file), size: file.size });
        } catch {
          setError(`${name} 을(를) 읽지 못했습니다.`);
        }
      }
      setAttachments((prev) => {
        const merged = [...prev, ...incoming];
        const total = merged.reduce((s, a) => s + a.size, 0);
        if (total > MAX_TOTAL_BYTES) {
          setError("첨부 파일 총 용량이 40MB를 초과합니다.");
          return prev;
        }
        return merged;
      });
    } finally {
      setReading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (dirInputRef.current) dirInputRef.current.value = "";
    }
  };

  const removeAttachment = (idx: number) =>
    setAttachments((prev) => prev.filter((_, i) => i !== idx));

  // --- Send -----------------------------------------------------------------

  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    if (providerNeedsKey(provider) && !apiKey.trim()) {
      setError("먼저 API 키를 입력하세요.");
      return;
    }
    const convId = activeConvId ?? newId();
    if (!activeConvId) setActiveConvId(convId);

    const decorated = contextJob
      ? `[작업 컨텍스트: job_id=${contextJob.id}, pipeline=${contextJob.pipeline}] ${text}`
      : text;
    const displayHistory: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(displayHistory);
    setInput("");
    setLoading(true);
    setError(null);

    const apiMessages = displayHistory.map((m, i) =>
      i === displayHistory.length - 1 ? { role: m.role, content: decorated } : { role: m.role, content: m.content },
    );
    const sentAttachments = attachments.map((a) => ({ name: a.name, base64: a.base64 }));
    try {
      const response = await chatWithModels(
        {
          provider,
          api_key: apiKey.trim(),
          model: model || undefined,
          messages: apiMessages,
          attachments: sentAttachments,
        },
        token,
      );
      const finalMessages: ChatMessage[] = [
        ...displayHistory,
        { role: "assistant", content: response.reply, toolCalls: response.tool_calls },
      ];
      setMessages(finalMessages);
      upsertConversation(convId, finalMessages);
      // Keep attachments across turns: the user may attach a file, discuss it,
      // then ask to run it a turn or two later. They persist until the user
      // removes them (✕) or starts a new chat. (Cleared on error is avoided so
      // a transient failure doesn't lose the upload.)
    } catch (err: any) {
      setError(err.message || "응답을 받지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const sortedConversations = useMemo(
    () => [...conversations].sort((a, b) => b.updatedAt - a.updatedAt),
    [conversations],
  );

  return (
    <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-3">
      {isOpen && (
        <div
          className={`${
            expanded ? "w-[760px]" : "w-[380px]"
          } max-w-[94vw] rounded-3xl border border-slate-200 bg-white shadow-2xl`}
        >
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">AI 도우미</p>
              <p className="text-xs text-slate-500">내 API 키로 모델을 실행하고 결과를 해석받으세요.</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                className="text-slate-400 hover:text-slate-600"
                title={expanded ? "작게" : "크게"}
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "🗕" : "🗖"}
              </button>
              <button className="text-slate-400 hover:text-slate-600" onClick={() => setIsOpen(false)}>
                ✕
              </button>
            </div>
          </div>

          {/* Conversation controls */}
          <div className="border-b border-slate-100 px-4 py-2">
            <div className="flex items-center gap-2">
              <button
                onClick={startNewChat}
                className="rounded-full bg-brand-600 px-3 py-1 text-xs font-semibold text-white"
              >
                + 새 대화
              </button>
              <button
                onClick={() => setShowConvList((v) => !v)}
                className="min-w-0 flex-1 truncate rounded-full border border-slate-200 px-3 py-1 text-left text-xs text-slate-600 hover:border-slate-300"
                title="저장된 대화 목록"
              >
                대화 목록 ({sortedConversations.length}) {showConvList ? "▲" : "▼"}
              </button>
            </div>
            {showConvList && (
              <div className="mt-2 rounded-2xl border border-slate-200 bg-white">
                {sortedConversations.length === 0 ? (
                  <p className="px-3 py-3 text-xs text-slate-400">저장된 대화가 없습니다.</p>
                ) : (
                  <>
                    <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
                      <span className="text-[11px] text-slate-400">저장된 대화</span>
                      <button
                        onClick={clearAllConversations}
                        className="text-[11px] font-semibold text-rose-500 hover:underline"
                      >
                        전체 삭제
                      </button>
                    </div>
                    <ul className="max-h-48 overflow-y-auto py-1">
                      {sortedConversations.map((c) => (
                        <li
                          key={c.id}
                          className={`flex items-center gap-2 px-3 py-1.5 text-xs ${
                            c.id === activeConvId ? "bg-brand-50" : ""
                          }`}
                        >
                          <button
                            onClick={() => {
                              loadConversation(c.id);
                              setShowConvList(false);
                            }}
                            className="min-w-0 flex-1 truncate text-left text-slate-700 hover:text-brand-700"
                            title={c.title}
                          >
                            {c.title}
                          </button>
                          <button
                            onClick={() => deleteConversation(c.id)}
                            title="이 대화 삭제"
                            className="shrink-0 rounded-full border border-rose-200 px-2 py-0.5 text-rose-500 hover:bg-rose-50"
                          >
                            ✕
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
          </div>

          <div className="space-y-3 px-4 py-3 text-sm text-slate-600">
            <div className="flex gap-2">
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value as ChatProvider)}
                className="rounded-2xl border border-slate-200 px-3 py-2 text-xs"
              >
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              {providerNeedsKey(provider) ? (
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => handleKeyChange(e.target.value)}
                  placeholder="API 키 (브라우저에만 저장)"
                  className="flex-1 rounded-2xl border border-slate-200 px-3 py-2 text-xs"
                />
              ) : (
                <span className="flex flex-1 items-center rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
                  키 불필요 · 자체 호스팅 모델
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <select
                value={model}
                onChange={(e) => handleModelChange(e.target.value)}
                className="min-w-0 flex-1 truncate rounded-2xl border border-slate-200 px-3 py-2 text-xs"
              >
                <option value="">모델: 기본값</option>
                {model && !models.includes(model) && <option value={model}>{model} (저장됨)</option>}
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <button
                onClick={() => void loadModels()}
                disabled={modelsLoading}
                title="사용 가능한 모델 불러오기"
                className="shrink-0 rounded-full border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
              >
                {modelsLoading ? "불러오는 중..." : models.length ? `🔄 ${models.length}` : "모델 불러오기"}
              </button>
            </div>

            {jobs && jobs.length > 0 && (
              <div>
                <label className="text-xs font-semibold text-slate-500">작업 참조 (선택)</label>
                <select
                  value={contextJobId ?? ""}
                  onChange={(e) => setContextJobId(e.target.value || null)}
                  className="mt-1 w-full rounded-2xl border border-slate-200 px-3 py-2 text-xs"
                >
                  <option value="">작업 참조 안 함</option>
                  {jobs.map((job) => (
                    <option key={job.id} value={job.id}>
                      {job.title} · {job.pipeline}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {insights?.active && (
              <div className="rounded-2xl border border-brand-100 bg-brand-50/50">
                <button
                  onClick={() => setInsightsOpen((v) => !v)}
                  className="flex w-full items-center justify-between px-3 py-2 text-xs font-semibold text-brand-700"
                >
                  <span>💡 학습된 가이드</span>
                  <span className="text-brand-400">{insightsOpen ? "▲" : "▼"}</span>
                </button>
                {insightsOpen && (
                  <div className="space-y-2 px-3 pb-3 text-[11px] text-slate-600">
                    {Object.keys(insights.payload.recommended_defaults).length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">추천 기본값</p>
                        <ul className="mt-1 space-y-0.5">
                          {Object.entries(insights.payload.recommended_defaults).map(([pipeline, params]) => (
                            <li key={pipeline}>
                              <span className="font-mono">{pipeline}</span>: {JSON.stringify(params)}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {insights.payload.warnings.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">실패 주의</p>
                        <ul className="mt-1 space-y-0.5">
                          {insights.payload.warnings.map((w, i) => (
                            <li key={i}>
                              <span className="font-mono">{w.pipeline}</span>: {w.message}{" "}
                              <span className="text-slate-400">({w.condition})</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {insights.payload.recipes.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">자주 쓰는 조합</p>
                        <ul className="mt-1 space-y-0.5">
                          {insights.payload.recipes.map((r, i) => (
                            <li key={i}>{r.goal}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <p className="text-[10px] text-slate-400">
                      전체 사용 기록의 집계(익명)입니다. 참고용이며 실행 전 확인하세요.
                    </p>
                  </div>
                )}
              </div>
            )}

            <div
              ref={threadRef}
              className={`${
                expanded ? "h-[62vh]" : "h-72"
              } resize-y overflow-y-auto rounded-2xl border border-slate-100 bg-slate-50 p-3 text-xs`}
            >
              {messages.length === 0 && (
                <p className="text-slate-400">
                  예: “esmfold로 ACDEFG 접어줘” · “첨부한 파일로 proteinmpnn 돌려줘” · “이 작업 결과 해석해줘”
                </p>
              )}
              {messages.map((message, index) => (
                <div key={index} className="mb-3">
                  <p
                    className={`font-semibold ${
                      message.role === "user" ? "text-slate-700" : "text-brand-600"
                    }`}
                  >
                    {message.role === "user" ? "사용자" : "어시스턴트"}
                  </p>
                  {message.toolCalls && message.toolCalls.length > 0 && (
                    <div className="my-1 flex flex-wrap gap-1">
                      {message.toolCalls.map((call, i) => (
                        <span
                          key={i}
                          className="rounded-full bg-brand-50 px-2 py-0.5 font-mono text-[10px] text-brand-700"
                        >
                          {toolChip(call)}
                        </span>
                      ))}
                    </div>
                  )}
                  {message.role === "assistant" ? (
                    <MarkdownMessage text={message.content} />
                  ) : (
                    <p className="whitespace-pre-wrap text-slate-600">{message.content}</p>
                  )}
                </div>
              ))}
            </div>

            {attachments.length > 0 && (
              <div className="space-y-1">
                <div className="flex items-center justify-between text-[10px] text-slate-400">
                  <span>첨부 {attachments.length}개 · 제거(✕) 전까지 유지되어 실행에 사용됩니다</span>
                  <button className="text-slate-400 hover:text-rose-500" onClick={() => setAttachments([])}>
                    모두 지우기
                  </button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {attachments.map((a, i) => (
                    <span
                      key={i}
                      className="flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600"
                    >
                      📎 {a.name} ({(a.size / 1024).toFixed(0)}KB)
                      <button className="text-slate-400 hover:text-rose-500" onClick={() => removeAttachment(i)}>
                        ✕
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center gap-2 text-xs">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => void addFiles(e.target.files)}
              />
              <input
                ref={dirInputRef}
                type="file"
                // @ts-expect-error non-standard directory-upload attribute
                webkitdirectory=""
                directory=""
                multiple
                className="hidden"
                onChange={(e) => void addFiles(e.target.files)}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="rounded-full border border-slate-200 px-3 py-1 text-slate-600 hover:bg-slate-100"
              >
                📎 파일
              </button>
              <button
                onClick={() => dirInputRef.current?.click()}
                className="rounded-full border border-slate-200 px-3 py-1 text-slate-600 hover:bg-slate-100"
              >
                📁 폴더
              </button>
              {reading && <span className="text-slate-400">파일 읽는 중...</span>}
            </div>

            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="모델을 실행하거나 결과를 물어보세요"
              className="h-20 w-full rounded-2xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
            />
            {error && <p className="text-xs text-rose-500">{error}</p>}
            <button
              className="w-full rounded-2xl bg-brand-600 py-2 text-sm font-semibold text-white disabled:opacity-50"
              disabled={loading || reading || !input.trim()}
              onClick={handleSend}
            >
              {loading ? "응답 생성 중..." : reading ? "파일 읽는 중..." : "보내기"}
            </button>
          </div>
        </div>
      )}
      <button
        className="rounded-full bg-brand-600 px-5 py-3 text-sm font-semibold text-white shadow-xl"
        onClick={() => setIsOpen((prev) => !prev)}
      >
        {isOpen ? "닫기" : "AI 도우미"}
      </button>
    </div>
  );
}

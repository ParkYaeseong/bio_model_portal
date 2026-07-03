"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { chatWithModels, ChatProvider, ChatToolCall, JobResponse } from "@/lib/api";

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

const PROVIDERS: { value: ChatProvider; label: string }[] = [
  { value: "anthropic", label: "Claude" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Gemini" },
];

const MAX_TOTAL_BYTES = 40 * 1024 * 1024; // 40 MB total across attachments

const providerKeyStore = (provider: ChatProvider) => `bmp_chat_key_${provider}`;

function toolChip(call: ChatToolCall): string {
  const ok = call.result?.ok !== false;
  const jobId = (call.result?.job_id as string | undefined) ?? undefined;
  const detail = jobId ? ` · ${jobId.slice(0, 8)}…` : "";
  return `🔧 ${call.name}${detail} ${ok ? "✓" : "✕"}`;
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

  const [provider, setProvider] = useState<ChatProvider>("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [contextJobId, setContextJobId] = useState<string | null>(initialJobId ?? null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  // Load persisted provider + key from the browser (never sent to our DB).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = (window.localStorage.getItem("bmp_chat_provider") as ChatProvider) || "anthropic";
    setProvider(saved);
    setApiKey(window.localStorage.getItem(providerKeyStore(saved)) || "");
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("bmp_chat_provider", provider);
    setApiKey(window.localStorage.getItem(providerKeyStore(provider)) || "");
  }, [provider]);

  useEffect(() => {
    if (initialJobId) setContextJobId(initialJobId);
  }, [initialJobId]);

  const handleKeyChange = (value: string) => {
    setApiKey(value);
    if (typeof window !== "undefined") window.localStorage.setItem(providerKeyStore(provider), value);
  };

  const contextJob = useMemo(() => jobs?.find((j) => j.id === contextJobId), [jobs, contextJobId]);

  const addFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setError(null);
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
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (dirInputRef.current) dirInputRef.current.value = "";
  };

  const removeAttachment = (idx: number) =>
    setAttachments((prev) => prev.filter((_, i) => i !== idx));

  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    if (!apiKey.trim()) {
      setError("먼저 API 키를 입력하세요.");
      return;
    }
    const decorated = contextJob
      ? `[작업 컨텍스트: job_id=${contextJob.id}, pipeline=${contextJob.pipeline}] ${text}`
      : text;
    const displayHistory: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(displayHistory);
    setInput("");
    setLoading(true);
    setError(null);

    // History sent to the API uses the decorated content for the new turn.
    const apiMessages = displayHistory.map((m, i) =>
      i === displayHistory.length - 1 ? { role: m.role, content: decorated } : { role: m.role, content: m.content },
    );
    const sentAttachments = attachments.map((a) => ({ name: a.name, base64: a.base64 }));
    try {
      const response = await chatWithModels(
        { provider, api_key: apiKey.trim(), messages: apiMessages, attachments: sentAttachments },
        token,
      );
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: response.reply, toolCalls: response.tool_calls },
      ]);
      setAttachments([]); // consumed by this turn
    } catch (err: any) {
      setError(err.message || "응답을 받지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-3">
      {isOpen && (
        <div className="w-[380px] max-w-[92vw] rounded-3xl border border-slate-200 bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">AI 도우미</p>
              <p className="text-xs text-slate-500">내 API 키로 모델을 실행하고 결과를 해석받으세요.</p>
            </div>
            <button className="text-slate-400" onClick={() => setIsOpen(false)}>
              ✕
            </button>
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
              <input
                type="password"
                value={apiKey}
                onChange={(e) => handleKeyChange(e.target.value)}
                placeholder="API 키 (브라우저에만 저장)"
                className="flex-1 rounded-2xl border border-slate-200 px-3 py-2 text-xs"
              />
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

            <div className="max-h-72 overflow-y-auto rounded-2xl border border-slate-100 bg-slate-50 p-3 text-xs">
              {messages.length === 0 && (
                <p className="text-slate-400">
                  예: “esmfold로 ACDEFG 접어줘” · “첨부한 파일로 alphafold 돌려줘” · “이 작업 결과 해석해줘”
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
                  <p className="whitespace-pre-wrap text-slate-600">{message.content}</p>
                </div>
              ))}
            </div>

            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {attachments.map((a, i) => (
                  <span
                    key={i}
                    className="flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600"
                  >
                    📎 {a.name}
                    <button className="text-slate-400 hover:text-rose-500" onClick={() => removeAttachment(i)}>
                      ✕
                    </button>
                  </span>
                ))}
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
              disabled={loading || !input.trim()}
              onClick={handleSend}
            >
              {loading ? "응답 생성 중..." : "보내기"}
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

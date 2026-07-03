"use client";

import { useEffect, useMemo, useState } from "react";
import {
  askAssistant,
  chatWithModels,
  ChatProvider,
  ChatToolCall,
  JobResponse,
} from "@/lib/api";

type Props = {
  token: string;
  jobs?: JobResponse[];
  initialJobId?: string | null;
};

type Mode = "chat" | "explain";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ChatToolCall[];
};

const PROVIDERS: { value: ChatProvider; label: string }[] = [
  { value: "anthropic", label: "Claude" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Gemini" },
];

const providerKeyStore = (provider: ChatProvider) => `bmp_chat_key_${provider}`;

function toolChip(call: ChatToolCall): string {
  const ok = call.result?.ok !== false;
  const jobId = (call.result?.job_id as string | undefined) ?? undefined;
  const detail = jobId ? ` · ${jobId.slice(0, 8)}…` : "";
  return `🔧 ${call.name}${detail} ${ok ? "✓" : "✕"}`;
}

export function AssistantWidget({ token, jobs, initialJobId }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("chat");

  // --- Execution chat state ---
  const [provider, setProvider] = useState<ChatProvider>("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");

  // --- Job-explain state ---
  const [activeJobId, setActiveJobId] = useState<string | null>(initialJobId ?? null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [explainInput, setExplainInput] = useState("");
  const [explainMessages, setExplainMessages] = useState<ChatMessage[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load persisted provider + key from the browser (never sent to our DB).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const savedProvider = (window.localStorage.getItem("bmp_chat_provider") as ChatProvider) || "anthropic";
    setProvider(savedProvider);
    setApiKey(window.localStorage.getItem(providerKeyStore(savedProvider)) || "");
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("bmp_chat_provider", provider);
    setApiKey(window.localStorage.getItem(providerKeyStore(provider)) || "");
  }, [provider]);

  const handleKeyChange = (value: string) => {
    setApiKey(value);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(providerKeyStore(provider), value);
    }
  };

  useEffect(() => {
    if (initialJobId) setActiveJobId(initialJobId);
  }, [initialJobId]);

  const activeJob = useMemo(() => jobs?.find((job) => job.id === activeJobId), [jobs, activeJobId]);

  useEffect(() => {
    if (!activeJob) {
      setSelectedArtifactId(null);
      return;
    }
    const firstArtifact = activeJob.artifacts[0];
    setSelectedArtifactId(firstArtifact ? firstArtifact.id : null);
  }, [activeJob]);

  const structureArtifacts = useMemo(() => {
    if (!activeJob) return [];
    return activeJob.artifacts.filter((artifact) => /\.(pdb|cif|json)$/i.test(artifact.file_name));
  }, [activeJob]);

  const handleChatSend = async () => {
    const text = chatInput.trim();
    if (!text) return;
    if (!apiKey.trim()) {
      setError("먼저 API 키를 입력하세요.");
      return;
    }
    const nextHistory: ChatMessage[] = [...chatMessages, { role: "user", content: text }];
    setChatMessages(nextHistory);
    setChatInput("");
    setLoading(true);
    setError(null);
    try {
      const response = await chatWithModels(
        {
          provider,
          api_key: apiKey.trim(),
          messages: nextHistory.map((m) => ({ role: m.role, content: m.content })),
        },
        token,
      );
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: response.reply, toolCalls: response.tool_calls },
      ]);
    } catch (err: any) {
      setError(err.message || "응답을 받지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const handleExplainSend = async () => {
    if (!activeJob || !explainInput.trim()) return;
    const userMessage = explainInput.trim();
    setExplainMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    setExplainInput("");
    setLoading(true);
    setError(null);
    try {
      const response = await askAssistant(
        { job_id: activeJob.id, message: userMessage, artifact_id: selectedArtifactId ?? undefined },
        token,
      );
      setExplainMessages((prev) => [...prev, { role: "assistant", content: response.reply }]);
    } catch (err: any) {
      setError(err.message || "챗봇 응답을 받지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const messages = mode === "chat" ? chatMessages : explainMessages;

  return (
    <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-3">
      {isOpen && (
        <div className="w-[380px] max-w-[92vw] rounded-3xl border border-slate-200 bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">AI 도우미</p>
              <p className="text-xs text-slate-500">모델을 직접 실행하고 결과를 해석받으세요.</p>
            </div>
            <button className="text-slate-400" onClick={() => setIsOpen(false)}>
              ✕
            </button>
          </div>

          {/* Mode toggle */}
          <div className="flex gap-1 px-4 pt-3">
            <button
              onClick={() => setMode("chat")}
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                mode === "chat" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500"
              }`}
            >
              실행 채팅
            </button>
            <button
              onClick={() => setMode("explain")}
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                mode === "explain" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500"
              }`}
            >
              결과 해석
            </button>
          </div>

          <div className="space-y-3 px-4 py-3 text-sm text-slate-600">
            {mode === "chat" && (
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
            )}

            {mode === "explain" && (
              <>
                <div>
                  <label className="text-xs font-semibold text-slate-500">작업 선택</label>
                  <select
                    value={activeJobId ?? ""}
                    onChange={(event) => setActiveJobId(event.target.value || null)}
                    className="mt-1 w-full rounded-2xl border border-slate-200 px-3 py-2"
                  >
                    <option value="">작업을 선택하세요</option>
                    {jobs?.map((job) => (
                      <option key={job.id} value={job.id}>
                        {job.title} · {job.pipeline}
                      </option>
                    ))}
                  </select>
                </div>
                {activeJob && structureArtifacts.length > 0 && (
                  <div>
                    <label className="text-xs font-semibold text-slate-500">참고 아티팩트</label>
                    <select
                      value={selectedArtifactId ?? ""}
                      onChange={(event) => setSelectedArtifactId(event.target.value || null)}
                      className="mt-1 w-full rounded-2xl border border-slate-200 px-3 py-2"
                    >
                      {structureArtifacts.map((artifact) => (
                        <option key={artifact.id} value={artifact.id}>
                          {artifact.file_name}
                        </option>
                      ))}
                      <option value="">선택 안 함</option>
                    </select>
                  </div>
                )}
              </>
            )}

            <div className="max-h-72 overflow-y-auto rounded-2xl border border-slate-100 bg-slate-50 p-3 text-xs">
              {messages.length === 0 && (
                <p className="text-slate-400">
                  {mode === "chat"
                    ? "예: esmfold로 ACDEFG 서열 접어줘 / 어떤 모델이 있어?"
                    : "질문을 입력하면 대화가 시작됩니다."}
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

            <textarea
              value={mode === "chat" ? chatInput : explainInput}
              onChange={(event) =>
                mode === "chat" ? setChatInput(event.target.value) : setExplainInput(event.target.value)
              }
              placeholder={
                mode === "chat"
                  ? "모델을 실행하거나 질문하세요"
                  : "예: PHASTEST 리포트에서 중요한 prophage를 요약해줘"
              }
              className="h-20 w-full rounded-2xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
            />
            {error && <p className="text-xs text-rose-500">{error}</p>}
            <button
              className="w-full rounded-2xl bg-brand-600 py-2 text-sm font-semibold text-white disabled:opacity-50"
              disabled={
                loading ||
                (mode === "chat" ? !chatInput.trim() : !activeJob || !explainInput.trim())
              }
              onClick={mode === "chat" ? handleChatSend : handleExplainSend}
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

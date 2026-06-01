"use client";

import { useEffect, useMemo, useState } from "react";
import { askAssistant, JobResponse } from "@/lib/api";

type Props = {
  token: string;
  jobs?: JobResponse[];
  initialJobId?: string | null;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export function AssistantWidget({ token, jobs, initialJobId }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(initialJobId ?? null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialJobId) {
      setActiveJobId(initialJobId);
    }
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

  const handleSend = async () => {
    if (!activeJob || !input.trim()) return;
    const userMessage = input.trim();
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    setInput("");
    setLoading(true);
    setError(null);
    try {
      const response = await askAssistant(
        {
          job_id: activeJob.id,
          message: userMessage,
          artifact_id: selectedArtifactId ?? undefined,
        },
        token,
      );
      setMessages((prev) => [...prev, { role: "assistant", content: response.reply }]);
    } catch (err: any) {
      setError(err.message || "챗봇 응답을 받지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-3">
      {isOpen && (
        <div className="w-[360px] max-w-[90vw] rounded-3xl border border-slate-200 bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">AI 도우미</p>
              <p className="text-xs text-slate-500">결과 해석 · 사용법 안내를 도움받으세요.</p>
            </div>
            <button className="text-slate-400" onClick={() => setIsOpen(false)}>
              ✕
            </button>
          </div>
          <div className="space-y-3 px-4 py-3 text-sm text-slate-600">
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
            <div className="max-h-60 overflow-y-auto rounded-2xl border border-slate-100 bg-slate-50 p-3 text-xs">
              {messages.length === 0 && <p className="text-slate-400">질문을 입력하면 대화가 시작됩니다.</p>}
              {messages.map((message, index) => (
                <div key={index} className="mb-3">
                  <p className={`font-semibold ${message.role === "user" ? "text-slate-700" : "text-brand-600"}`}>
                    {message.role === "user" ? "사용자" : "어시스턴트"}
                  </p>
                  <p className="whitespace-pre-wrap text-slate-600">{message.content}</p>
                </div>
              ))}
            </div>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="예: PHASTEST 리포트에서 중요한 prophage를 요약해줘"
              className="h-20 w-full rounded-2xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
            />
            {error && <p className="text-xs text-rose-500">{error}</p>}
            <button
              className="w-full rounded-2xl bg-brand-600 py-2 text-sm font-semibold text-white disabled:opacity-50"
              disabled={!activeJob || !input.trim() || loading}
              onClick={handleSend}
            >
              {loading ? "응답 생성 중..." : "질문 보내기"}
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

"use client";

import { useState } from "react";
import useSWR from "swr";

import { McpToken, createMcpToken, listMcpTokens, revokeMcpToken } from "@/lib/api";

const FALLBACK_MCP_URL = "https://biomodel.k-biofoundrycopilot.duckdns.org/mcp";

const mcpEndpointUrl = () =>
  typeof window !== "undefined" ? `${window.location.origin}/mcp` : FALLBACK_MCP_URL;

const buildConfigSnippet = (rawToken: string) =>
  JSON.stringify(
    {
      mcpServers: {
        "bio-model-portal": {
          url: mcpEndpointUrl(),
          headers: { Authorization: `Bearer ${rawToken || "<YOUR_TOKEN>"}` },
        },
      },
    },
    null,
    2
  );

export default function McpSettingsPage() {
  // Identity travels via the gateway-injected header (Caddy forward_auth SSO);
  // the browser holds no bearer token, so pass an empty string like other pages do.
  const token = "";

  const { data, isLoading, mutate } = useSWR(["mcp-tokens"], () => listMcpTokens(token));
  const tokens = data?.tokens ?? [];

  const [newTokenName, setNewTokenName] = useState("");
  const [creating, setCreating] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [rawToken, setRawToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCreate = async () => {
    const name = newTokenName.trim() || "claude-desktop";
    setError(null);
    setCreating(true);
    try {
      const created = await createMcpToken(token, name);
      setRawToken(created.token);
      setCopied(false);
      setNewTokenName("");
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "토큰 생성 중 오류가 발생했습니다.");
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id: string) => {
    setError(null);
    setRevokingId(id);
    try {
      await revokeMcpToken(token, id);
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "토큰 해지 중 오류가 발생했습니다.");
    } finally {
      setRevokingId(null);
    }
  };

  const handleCopy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-3xl px-6 py-10">
        <h1 className="text-2xl font-semibold text-slate-900">AI 연결 (MCP)</h1>
        <p className="mt-2 text-sm text-slate-500">
          외부 AI(Claude/Codex/Gemini)를 MCP로 연결해 포탈 모델을 실행·설명하게 합니다.
        </p>

        {rawToken && (
          <div className="mt-6 rounded-2xl border border-amber-300 bg-amber-50 p-6">
            <p className="text-sm font-semibold text-amber-800">토큰이 생성되었습니다</p>
            <p className="mt-1 font-mono text-sm text-amber-900 break-all">{rawToken}</p>
            <p className="mt-2 text-xs text-amber-700">
              이 토큰은 다시 표시되지 않습니다. 지금 복사해 두세요.
            </p>
            <div className="mt-3 flex items-center gap-3">
              <button
                onClick={() => void handleCopy(rawToken)}
                className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-white"
              >
                복사
              </button>
              <button
                onClick={() => {
                  setRawToken(null);
                  setCopied(false);
                }}
                className="rounded-full border border-amber-300 px-4 py-2 text-sm font-semibold text-amber-800"
              >
                닫기
              </button>
              {copied && <span className="text-xs text-amber-700">복사되었습니다.</span>}
            </div>
          </div>
        )}

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">토큰 생성</h2>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <input
              type="text"
              value={newTokenName}
              onChange={(e) => setNewTokenName(e.target.value)}
              placeholder="claude-desktop"
              className="w-64 rounded-full border border-slate-200 px-4 py-2 text-sm"
            />
            <button
              onClick={() => void handleCreate()}
              disabled={creating}
              className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {creating ? "생성 중..." : "토큰 생성"}
            </button>
          </div>
          {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">발급된 토큰</h2>
          {isLoading && <p className="mt-3 text-sm text-slate-500">불러오는 중...</p>}
          {!isLoading && tokens.length === 0 && (
            <p className="mt-3 text-sm text-slate-500">아직 발급된 토큰이 없습니다.</p>
          )}
          {tokens.length > 0 && (
            <ul className="mt-4 divide-y divide-slate-100">
              {tokens.map((t: McpToken) => (
                <li key={t.id} className="flex items-center justify-between py-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">{t.name}</p>
                    <p className="mt-1 font-mono text-xs text-slate-500">{t.prefix}…</p>
                    <p className="mt-1 text-xs text-slate-400">
                      생성일: {new Date(t.created_at).toLocaleString("ko-KR")} · 마지막 사용:{" "}
                      {t.last_used_at ? new Date(t.last_used_at).toLocaleString("ko-KR") : "—"}
                    </p>
                  </div>
                  <button
                    onClick={() => void handleRevoke(t.id)}
                    disabled={revokingId === t.id}
                    className="rounded-full border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-600 disabled:opacity-50"
                  >
                    {revokingId === t.id ? "해지 중..." : "해지"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">연결 방법</h2>
          <p className="mt-2 text-sm text-slate-500">
            MCP 엔드포인트: <span className="font-mono text-slate-700">{mcpEndpointUrl()}</span>
          </p>
          <p className="mt-3 text-sm text-slate-500">
            아래 설정을 Claude/Codex/Gemini 등 MCP 클라이언트 설정에 붙여넣고 <span className="font-mono">&lt;YOUR_TOKEN&gt;</span>을 위에서 발급한 토큰으로 교체하세요.
          </p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-900 p-4 font-mono text-xs text-slate-100">
            {buildConfigSnippet(rawToken ?? "")}
          </pre>
        </div>
      </section>
    </main>
  );
}

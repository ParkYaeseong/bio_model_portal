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

        {/* The `files` contract is the one thing agents get wrong: an LLM will
            happily send files:[{name: "data/x.pdb"}] believing the server reads
            that path. It never did -- the entry was dropped and the job failed
            later in the gateway -- so the accepted forms are spelled out here. */}
        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">파일 입력 규약</h2>
          <p className="mt-2 text-sm text-slate-500">
            AntiFold·ProteinMPNN·PPIformer처럼 구조(PDB/CIF)를 받는 모델은{" "}
            <span className="font-mono">run_model</span>의 <span className="font-mono">files</span>로 파일을
            넘깁니다. <strong>
              <span className="font-mono">name</span>은 파일 이름일 뿐, 서버가 읽는 경로가 아닙니다.
            </strong>{" "}
            각 항목은 아래 네 가지 중 하나로 실제 내용을 반드시 실어야 하며, 없으면 제출 단계에서 바로
            거부됩니다.
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
                  <th className="py-2 pr-4 font-semibold">형태</th>
                  <th className="py-2 font-semibold">쓰는 경우</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-600">
                <tr>
                  <td className="py-2 pr-4 font-mono text-xs">{`{"path":"ab.pdb"}`}</td>
                  <td className="py-2">
                    <strong>포털 서버에서 도는 클라이언트는 이것.</strong>{" "}
                    <span className="font-mono">list_files</span>가 알려주는 본인 워크스페이스 폴더로{" "}
                    <span className="font-mono">cp</span> 한 뒤 파일명만 넘기면 됩니다 — 인코딩·토큰 비용 없음.
                  </td>
                </tr>
                <tr>
                  <td className="py-2 pr-4 font-mono text-xs">{`{"name":"ab.pdb","text":"ATOM ..."}`}</td>
                  <td className="py-2">50KB 이하 텍스트 파일 (PDB·CIF·FASTA·SDF)</td>
                </tr>
                <tr>
                  <td className="py-2 pr-4 font-mono text-xs">{`{"file_id":"ab.pdb"}`}</td>
                  <td className="py-2">
                    원격 클라이언트용. <span className="font-mono">upload_file</span>로 한 번 올리고 재사용,
                    큰 파일은 <span className="font-mono">append: true</span>로 나눠 올린 뒤 반환된{" "}
                    <span className="font-mono">sha256</span>로 무결성 확인.
                  </td>
                </tr>
                <tr>
                  <td className="py-2 pr-4 font-mono text-xs">{`{"name":"ab.pdb","base64":"..."}`}</td>
                  <td className="py-2">소용량 바이너리 파일</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">
            큰 구조를 base64로 인라인하지 마세요. 227KB PDB는 base64로 약 303KB(≈75k 토큰)이고,
            조각내서 여러 번 올려도 총 비용은 같습니다. 같은 서버에서 도는 에이전트라면{" "}
            <span className="font-mono">cp</span> + <span className="font-mono">path</span>가 항상 가장 빠릅니다.
          </p>
          <p className="mt-4 text-sm text-slate-500">
            결과는 <span className="font-mono">job_result</span>로 파일 목록을 보고{" "}
            <span className="font-mono">download_artifact(job_id, artifact_id)</span>로 내용까지 바로 읽을 수
            있습니다(<span className="font-mono">save_to_workspace: true</span>를 주면 다음 모델의 입력으로
            그대로 이어서 쓸 수 있습니다).
          </p>
          <p className="mt-3 text-xs text-slate-400">
            서열 입력 모델(AlphaFold2/3·Boltz-2·ColabFold·ESMFold 등)은 <span className="font-mono">sequence</span>
            를 쓰고, 복합체는 체인을 <span className="font-mono">:</span>로 이어 붙입니다. 필수 입력이 빠지면
            제출 전에 무엇이 필요한지 알려주는 에러가 돌아옵니다.
          </p>
        </div>

        {/* Runcell Science runs on each member's own machine: it drives the
            Claude Code / Codex CLI that person is already signed in to, so one
            shared copy would mean one shared seat and one shared token. */}
        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">Runcell Science (로컬 설치)</h2>
          <p className="mt-2 text-sm text-slate-500">
            노트북·플롯·분석 코드를 대화로 돌리는 연구용 에이전트입니다. 위 MCP 설정을 붙여넣으면 이
            포털의 모델 실행·결과 조회 도구(<span className="font-mono">list_models</span>,{" "}
            <span className="font-mono">run_model</span>, <span className="font-mono">job_result</span> 등)를
            그대로 씁니다.
          </p>
          <p className="mt-3 text-sm text-slate-500">
            에이전트는 <strong>각자 자기 PC에</strong> 설치합니다. 이미 로그인된 Claude Code / Codex CLI를
            그대로 사용하므로 별도 API 키가 필요하지 않습니다.
          </p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-900 p-4 font-mono text-xs text-slate-100">
{`# 1) 설치 (Apache-2.0)
git clone https://github.com/runcell-ai/runcell-science.git
cd runcell-science && ./scripts/dev.sh      # http://127.0.0.1:27183

# 2) 과학 skill 추가 (선택)
npx skills add K-Dense-AI/scientific-agent-skills

# 3) 위 "연결 방법"의 JSON을 Connectors 패널 > Import JSON 에 붙여넣기`}
          </pre>
          <p className="mt-3 text-xs text-slate-400">
            토큰은 계정에 귀속되므로 공유하지 마세요. 사람마다 자기 토큰을 발급해 쓰면 GPU 사용량도
            각자에게 정확히 기록됩니다.
          </p>
        </div>
      </section>
    </main>
  );
}

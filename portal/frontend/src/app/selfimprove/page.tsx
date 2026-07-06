"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import {
  ImprovementArtifact,
  activateArtifact,
  fetchSelfimproveAdmin,
  listArtifacts,
  rejectArtifact,
} from "@/lib/api";

// Identity travels via the gateway-injected SSO header; the browser holds no
// bearer token (same pattern as the other pages).
const TOKEN = "";

const STATUS_STYLE: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-700",
  proposed: "bg-amber-100 text-amber-700",
  rejected: "bg-slate-100 text-slate-500",
};

function ArtifactCard({
  artifact,
  busy,
  onActivate,
  onReject,
}: {
  artifact: ImprovementArtifact;
  busy: boolean;
  onActivate: () => void;
  onReject: () => void;
}) {
  const p = artifact.payload || { recommended_defaults: {}, warnings: [], recipes: [] };
  const defaults = Object.entries(p.recommended_defaults || {});
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_STYLE[artifact.status] || "bg-slate-100 text-slate-500"}`}>
              {artifact.status}
            </span>
            <span className="text-sm font-semibold text-slate-900">{artifact.summary || "(no summary)"}</span>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            {artifact.created_at ? new Date(artifact.created_at).toLocaleString("ko-KR") : "—"} ·{" "}
            {JSON.stringify(artifact.stats || {})}
          </p>
        </div>
        {artifact.status !== "rejected" && (
          <div className="flex shrink-0 gap-2">
            {artifact.status !== "active" && (
              <button
                onClick={onActivate}
                disabled={busy}
                className="rounded-full bg-brand-600 px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
              >
                {busy ? "..." : "활성화"}
              </button>
            )}
            <button
              onClick={onReject}
              disabled={busy}
              className="rounded-full border border-rose-200 px-4 py-1.5 text-xs font-semibold text-rose-600 disabled:opacity-50"
            >
              반려
            </button>
          </div>
        )}
      </div>

      <div className="mt-4 grid gap-3 text-xs text-slate-600 sm:grid-cols-3">
        <div>
          <p className="font-semibold text-slate-500">추천 기본값 ({defaults.length})</p>
          <ul className="mt-1 space-y-0.5">
            {defaults.length === 0 && <li className="text-slate-400">없음</li>}
            {defaults.map(([pipeline, params]) => (
              <li key={pipeline}>
                <span className="font-mono">{pipeline}</span>: {JSON.stringify(params)}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="font-semibold text-slate-500">실패 주의 ({p.warnings?.length || 0})</p>
          <ul className="mt-1 space-y-0.5">
            {(!p.warnings || p.warnings.length === 0) && <li className="text-slate-400">없음</li>}
            {(p.warnings || []).map((w, i) => (
              <li key={i}>
                <span className="font-mono">{w.pipeline}</span>: {w.message}{" "}
                <span className="text-slate-400">({w.condition})</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="font-semibold text-slate-500">조합 ({p.recipes?.length || 0})</p>
          <ul className="mt-1 space-y-0.5">
            {(!p.recipes || p.recipes.length === 0) && <li className="text-slate-400">없음</li>}
            {(p.recipes || []).map((r, i) => (
              <li key={i}>{r.goal}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

export default function SelfImprovePage() {
  const { data: adminData, isLoading: adminLoading } = useSWR(["si-admin"], () => fetchSelfimproveAdmin(TOKEN));
  const isAdmin = adminData?.is_admin === true;

  const { data, isLoading, mutate } = useSWR(isAdmin ? ["si-artifacts"] : null, () => listArtifacts(TOKEN));
  const artifacts = data?.artifacts ?? [];

  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (id: string, fn: (t: string, i: string) => Promise<unknown>) => {
    setBusyId(id);
    setError(null);
    try {
      await fn(TOKEN, id);
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청이 실패했습니다.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-4xl px-6 py-10">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">자가개선 검토</h1>
          <Link href="/" className="rounded-full border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-100">
            ← 홈
          </Link>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          전체 사용 기록의 집계(익명)에서 제안된 가이드입니다. <strong>활성화</strong>하면 모든 사용자의 챗봇과 &ldquo;학습된 가이드&rdquo;에 반영됩니다. 활성은 항상 1개만 유지됩니다.
        </p>

        {adminLoading && <p className="mt-8 text-sm text-slate-500">확인 중...</p>}

        {!adminLoading && !isAdmin && (
          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
            이 페이지는 관리자만 접근할 수 있습니다. (설정: <span className="font-mono">SELFIMPROVE_ADMIN_USERS</span>)
          </div>
        )}

        {isAdmin && (
          <div className="mt-6 space-y-4">
            {error && <p className="text-sm text-rose-600">{error}</p>}
            {isLoading && <p className="text-sm text-slate-500">불러오는 중...</p>}
            {!isLoading && artifacts.length === 0 && (
              <p className="text-sm text-slate-500">아직 제안된 아티팩트가 없습니다. 야간 분석 후 생성됩니다.</p>
            )}
            {artifacts.map((a) => (
              <ArtifactCard
                key={a.id}
                artifact={a}
                busy={busyId === a.id}
                onActivate={() => act(a.id, activateArtifact)}
                onReject={() => act(a.id, rejectArtifact)}
              />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { CompatGraph, getChainsCompat } from "@/lib/api";

// Pipeline key -> short label for badges/nodes.
const LABELS: Record<string, string> = {
  rfdiffusion: "RFdiffusion",
  proteinmpnn: "ProteinMPNN",
  colabfold: "ColabFold",
  alphafold: "AlphaFold2",
  esmfold: "ESMFold",
  esmfold2: "ESMFold2",
  bioemu: "BioEmu",
  diffdock: "DiffDock",
  rosetta_relax: "Rosetta Relax",
  mmseqs: "MSA (mmseqs)",
};
const label = (k: string) => LABELS[k] ?? k;

// Lay nodes out on a circle so every edge is drawable without a graph lib.
function layout(keys: string[], cx: number, cy: number, r: number) {
  const pos: Record<string, { x: number; y: number }> = {};
  keys.forEach((k, i) => {
    const a = (2 * Math.PI * i) / keys.length - Math.PI / 2;
    pos[k] = { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
  return pos;
}

export default function ChainsPage() {
  const token = ""; // SSO via gateway header, like other pages
  const { data, isLoading, error } = useSWR<CompatGraph>(["chains-compat"], () => getChainsCompat(token));
  const [selected, setSelected] = useState<string | null>(null);

  const W = 720, H = 720, CX = 360, CY = 360, R = 260;
  const keys = useMemo(() => (data ? data.nodes.map((n) => n.key) : []), [data]);
  const pos = useMemo(() => layout(keys, CX, CY, R), [keys]);

  const edges = data?.edges ?? [];
  const isActiveEdge = (from: string) => selected === null || from === selected;
  const examplesForSelected = (data?.examples ?? []).filter(
    (ex) => selected === null || ex.steps.includes(selected)
  );

  return (
    <main className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white/80 px-6 py-5 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div>
            <p className="text-sm text-slate-500">Bio Model Portal</p>
            <h1 className="text-2xl font-semibold text-slate-900">연결 가이드</h1>
          </div>
          <Link href="/" className="rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100">
            돌아가기
          </Link>
        </div>
      </header>

      <section className="mx-auto grid max-w-7xl gap-6 px-6 py-8 lg:grid-cols-[720px_1fr]">
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="mb-2 text-sm text-slate-500">
            노드를 클릭하면 그 모델의 출력이 이어질 수 있는 대상이 강조됩니다.
          </p>
          {isLoading && <p className="p-8 text-slate-500">불러오는 중…</p>}
          {error && <p className="p-8 text-red-600">그래프를 불러오지 못했습니다.</p>}
          {data && (
            <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
              {edges.map((e, i) => {
                const a = pos[e.from], b = pos[e.to];
                if (!a || !b) return null;
                return (
                  <line
                    key={i}
                    x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke={isActiveEdge(e.from) ? "#6366f1" : "#e2e8f0"}
                    strokeWidth={isActiveEdge(e.from) ? 1.5 : 0.75}
                    markerEnd="url(#arrow)"
                  />
                );
              })}
              <defs>
                <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                  <path d="M0,0 L7,3 L0,6 Z" fill="#6366f1" />
                </marker>
              </defs>
              {keys.map((k) => {
                const p = pos[k];
                const on = selected === k;
                return (
                  <g key={k} className="cursor-pointer" onClick={() => setSelected(on ? null : k)}>
                    <circle cx={p.x} cy={p.y} r={on ? 30 : 26}
                      fill={on ? "#6366f1" : "#fff"} stroke="#6366f1" strokeWidth={1.5} />
                    <text x={p.x} y={p.y + 44} textAnchor="middle"
                      className="text-[11px]" fill="#334155">{label(k)}</text>
                  </g>
                );
              })}
            </svg>
          )}
        </div>

        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">예시 체인</h2>
          {examplesForSelected.map((ex) => (
            <div key={ex.title} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h3 className="font-semibold text-slate-900">{ex.title}</h3>
              <div className="mt-2 flex flex-wrap items-center gap-1 text-sm">
                {ex.steps.map((s, i) => (
                  <span key={s} className="flex items-center gap-1">
                    <span className="rounded-full bg-brand-100 px-3 py-1 text-brand-700">{label(s)}</span>
                    {i < ex.steps.length - 1 && <span className="text-slate-400">→</span>}
                  </span>
                ))}
              </div>
              <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                <span className="font-medium text-slate-500">도우미에게: </span>“{ex.prompt}”
              </p>
            </div>
          ))}
          {examplesForSelected.length === 0 && (
            <p className="text-sm text-slate-500">선택한 모델이 포함된 예시가 없습니다.</p>
          )}
        </div>
      </section>
    </main>
  );
}

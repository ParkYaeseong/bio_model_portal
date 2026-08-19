"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { CompatGraph, ExampleChain, getChainsCompat } from "@/lib/api";

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

// Semantic role -> what the chained output actually carries.
const ROLE_KO: Record<string, string> = {
  structure: "구조 파일",
  sequence: "서열",
  complex: "도킹 복합체",
  msa: "MSA",
};

function ExampleCard({ ex }: { ex: ExampleChain }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
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
  );
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
  // Every connection the selected model's output can feed into (from the graph
  // edges — this is the complete set, not just the curated examples).
  const outgoing = selected ? edges.filter((e) => e.from === selected) : [];

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
            <span className="text-slate-400"> (점선 = 고급 조합)</span>
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
                    strokeDasharray={e.advanced ? "5 4" : undefined}
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
          {selected ? (
            <>
              <div className="rounded-2xl border border-brand-100 bg-white p-5 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-900">
                  「{label(selected)}」 출력으로 이어서 할 수 있는 것
                </h2>
                {outgoing.length > 0 ? (
                  <ul className="mt-3 space-y-2">
                    {outgoing.map((e) => (
                      <li key={e.to} className="flex items-center gap-2 text-sm">
                        <span className="rounded-full bg-brand-100 px-3 py-1 text-brand-700">{label(e.to)}</span>
                        {e.advanced && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">고급</span>
                        )}
                        <span className="text-xs text-slate-400">{ROLE_KO[e.role] ?? e.role} 전달</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-3 text-sm text-slate-500">
                    이 모델의 출력은 체인의 종료점입니다 (이어지는 다음 단계 없음).
                  </p>
                )}
                <p className="mt-3 text-xs text-slate-400">
                  챗봇에게 “이 {label(selected)} 잡 결과로 …를 돌려줘”라고 하면 서버가 자동으로 이어줍니다.
                </p>
              </div>
              {examplesForSelected.length > 0 && (
                <>
                  <h2 className="text-lg font-semibold text-slate-900">관련 예시 체인</h2>
                  {examplesForSelected.map((ex) => (
                    <ExampleCard key={ex.title} ex={ex} />
                  ))}
                </>
              )}
            </>
          ) : (
            <>
              <h2 className="text-lg font-semibold text-slate-900">예시 체인</h2>
              <p className="text-sm text-slate-500">
                노드를 클릭하면 그 모델이 이어질 수 있는 모든 대상이 여기 표시됩니다.
              </p>
              {examplesForSelected.map((ex) => (
                <ExampleCard key={ex.title} ex={ex} />
              ))}
            </>
          )}
        </div>
      </section>
    </main>
  );
}

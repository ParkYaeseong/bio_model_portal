"use client";

import { useMemo, useState } from "react";
import Link from "next/link";

import {
  AuthError,
  BinderResult,
  BinderScoreResponse,
  scoreBinders,
} from "@/lib/api";

// Six segments, because that is the shape of the epitope we keep fixed while the
// rest of the antigen scaffold is regenerated.
const SEGMENT_COUNT = 6;

const METRICS: { name: string; label: string; higherIsBetter: boolean }[] = [
  { name: "ipsae", label: "ipSAE (권장)", higherIsBetter: true },
  { name: "iptm_pae", label: "ipTM (PAE 재계산)", higherIsBetter: true },
  { name: "pdockq", label: "pDockQ", higherIsBetter: true },
  { name: "interface_plddt", label: "인터페이스 pLDDT", higherIsBetter: true },
  { name: "plddt_mean", label: "평균 pLDDT", higherIsBetter: true },
  { name: "pae_interaction", label: "인터페이스 PAE (낮을수록 좋음)", higherIsBetter: false },
  { name: "epitope_rmsd", label: "에피토프 RMSD (낮을수록 좋음)", higherIsBetter: false },
];

type Segment = { chain: string; start: string; end: string };

const emptySegments = (): Segment[] =>
  Array.from({ length: SEGMENT_COUNT }, () => ({ chain: "A", start: "", end: "" }));

function segmentsToSpec(segments: Segment[]): string {
  return segments
    .filter((s) => s.start.trim() !== "")
    .map((s) => {
      const chain = s.chain.trim() || "A";
      const start = s.start.trim();
      const end = s.end.trim();
      return end && end !== start ? `${chain}:${start}-${end}` : `${chain}:${start}`;
    })
    .join(",");
}

function fmt(value: number | null | undefined, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

async function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("파일을 읽을 수 없습니다."));
    reader.readAsText(file);
  });
}

export default function AntigenValidationPage() {
  const [structure, setStructure] = useState("");
  const [structureName, setStructureName] = useState("");
  const [scoresText, setScoresText] = useState("");
  const [scoresName, setScoresName] = useState("");
  const [reference, setReference] = useState("");
  const [referenceName, setReferenceName] = useState("");
  const [segments, setSegments] = useState<Segment[]>(emptySegments);
  const [metric, setMetric] = useState("ipsae");
  const [cutoff, setCutoff] = useState("0.3");
  const [paeCutoff, setPaeCutoff] = useState("15");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
  const [response, setResponse] = useState<BinderScoreResponse | null>(null);

  const epitopeSpec = useMemo(() => segmentsToSpec(segments), [segments]);
  const selectedMetric = METRICS.find((m) => m.name === metric) ?? METRICS[0];

  const results: BinderResult[] = useMemo(() => {
    if (!response) return [];
    if (Array.isArray(response.results)) return response.results;
    return [response];
  }, [response]);

  const updateSegment = (index: number, patch: Partial<Segment>) => {
    setSegments((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };

  const pickFile = async (
    file: File | undefined,
    setText: (v: string) => void,
    setName: (v: string) => void,
  ) => {
    if (!file) return;
    try {
      setText(await readFile(file));
      setName(file.name);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const submit = async () => {
    setError(null);
    setAuthRequired(false);
    setResponse(null);

    if (!structure.trim()) {
      setError("항원 구조(PDB 또는 mmCIF)를 넣어주세요.");
      return;
    }

    let scores: unknown;
    if (scoresText.trim()) {
      try {
        scores = JSON.parse(scoresText);
      } catch {
        setError("scores JSON을 파싱할 수 없습니다. ColabFold의 scores_rank_001 파일을 넣어주세요.");
        return;
      }
    }

    const parsedCutoff = cutoff.trim() === "" ? undefined : Number(cutoff);
    if (parsedCutoff !== undefined && !Number.isFinite(parsedCutoff)) {
      setError("컷오프는 숫자여야 합니다.");
      return;
    }

    setBusy(true);
    try {
      const out = await scoreBinders({
        candidates: [
          { id: structureName || "candidate_1", structure, ...(scores ? { scores } : {}) },
        ],
        ...(reference.trim() ? { reference_structure: reference } : {}),
        ...(epitopeSpec ? { epitope: epitopeSpec } : {}),
        primary_metric: metric,
        ...(parsedCutoff !== undefined ? { cutoff: parsedCutoff } : {}),
        pae_cutoff: Number(paeCutoff) || 15,
      });
      setResponse(out);
    } catch (exc) {
      if (exc instanceof AuthError) {
        setAuthRequired(true);
      } else {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-8">
        <Link href="/" className="text-sm text-slate-500 hover:text-slate-800">
          ← 포털로
        </Link>
        <h1 className="mt-2 text-2xl font-semibold text-slate-900">항원 검증 (결합 스코어링)</h1>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          에피토프를 고정하고 재생성한 항원이 여전히 결합하는지 인터페이스 신뢰도로 판정합니다.
          용해도(SoluProt)가 아니라 ipSAE·ipTM·pDockQ를 씁니다. ipSAE는 PAE가 컷오프 아래인
          잔기쌍만 사용하므로, 무질서 영역이 점수를 끌어내리지 않습니다.
        </p>
      </div>

      <section className="space-y-6 rounded-lg border border-slate-200 bg-white p-6">
        <div className="grid gap-4 md:grid-cols-3">
          <FileField
            label="항원 구조 (필수)"
            hint="PDB 또는 mmCIF"
            name={structureName}
            accept=".pdb,.cif,.mmcif,.txt"
            onPick={(f) => pickFile(f, setStructure, setStructureName)}
          />
          <FileField
            label="scores JSON"
            hint="PAE 포함 — 없으면 인터페이스 지표 불가"
            name={scoresName}
            accept=".json"
            onPick={(f) => pickFile(f, setScoresText, setScoresName)}
          />
          <FileField
            label="참조 항원"
            hint="에피토프 RMSD 기준 (선택)"
            name={referenceName}
            accept=".pdb,.cif,.mmcif,.txt"
            onPick={(f) => pickFile(f, setReference, setReferenceName)}
          />
        </div>

        <div>
          <h2 className="text-sm font-medium text-slate-900">고정 에피토프 구간 (최대 {SEGMENT_COUNT}개)</h2>
          <p className="mt-1 text-xs text-slate-500">
            비워두면 에피토프 지표를 건너뜁니다. 끝 잔기를 비우면 단일 잔기로 처리합니다.
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {segments.map((seg, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <span className="w-5 text-xs text-slate-400">{i + 1}</span>
                <input
                  aria-label={`구간 ${i + 1} 체인`}
                  className="w-12 rounded border border-slate-300 px-2 py-1.5 text-sm"
                  value={seg.chain}
                  onChange={(e) => updateSegment(i, { chain: e.target.value })}
                />
                <input
                  aria-label={`구간 ${i + 1} 시작`}
                  className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                  placeholder="시작"
                  inputMode="numeric"
                  value={seg.start}
                  onChange={(e) => updateSegment(i, { start: e.target.value })}
                />
                <span className="text-slate-400">–</span>
                <input
                  aria-label={`구간 ${i + 1} 끝`}
                  className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                  placeholder="끝"
                  inputMode="numeric"
                  value={seg.end}
                  onChange={(e) => updateSegment(i, { end: e.target.value })}
                />
              </div>
            ))}
          </div>
          <p className="mt-2 font-mono text-xs text-slate-500">
            {epitopeSpec || "(지정 없음)"}
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block">
            <span className="text-sm font-medium text-slate-900">판정 지표</span>
            <select
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
            >
              {METRICS.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-900">
              컷오프 ({selectedMetric.higherIsBetter ? "이상 통과" : "이하 통과"})
            </span>
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
              value={cutoff}
              inputMode="decimal"
              onChange={(e) => setCutoff(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-900">PAE 컷오프 (Å)</span>
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
              value={paeCutoff}
              inputMode="decimal"
              onChange={(e) => setPaeCutoff(e.target.value)}
            />
          </label>
        </div>

        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? "채점 중…" : "채점하기"}
        </button>

        {authRequired && (
          <p className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            세션이 만료되었습니다.{" "}
            <Link href="/login" className="underline">
              로그인 하세요
            </Link>
            .
          </p>
        )}
        {error && (
          <p className="rounded border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-900">
            {error}
          </p>
        )}
      </section>

      {results.length > 0 && (
        <section className="mt-8 rounded-lg border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">결과</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                <tr>
                  <th className="py-2 pr-4">후보</th>
                  <th className="py-2 pr-4">판정</th>
                  <th className="py-2 pr-4">{selectedMetric.label}</th>
                  <th className="py-2 pr-4">ipSAE</th>
                  <th className="py-2 pr-4">ipTM(PAE)</th>
                  <th className="py-2 pr-4">pDockQ</th>
                  <th className="py-2 pr-4">인터페이스 pLDDT</th>
                  <th className="py-2 pr-4">에피토프 RMSD</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={r.id ?? i} className="border-b border-slate-100">
                    <td className="py-2 pr-4 font-mono text-xs">{r.id ?? `candidate_${i + 1}`}</td>
                    <td className="py-2 pr-4">
                      {r.passed === true && (
                        <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
                          통과
                        </span>
                      )}
                      {r.passed === false && (
                        <span className="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-800">
                          탈락
                        </span>
                      )}
                      {(r.passed === null || r.passed === undefined) && (
                        <span
                          className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
                          title={r.reason ?? undefined}
                        >
                          판정 불가
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-4 font-medium">{fmt(r.primary_value)}</td>
                    <td className="py-2 pr-4">{fmt(r.metrics?.ipsae)}</td>
                    <td className="py-2 pr-4">{fmt(r.metrics?.iptm_pae)}</td>
                    <td className="py-2 pr-4">{fmt(r.metrics?.pdockq)}</td>
                    <td className="py-2 pr-4">{fmt(r.metrics?.interface_plddt, 1)}</td>
                    <td className="py-2 pr-4">{fmt(r.metrics?.epitope_rmsd, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {results.some((r) => r.reason) && (
            <ul className="mt-4 space-y-1 text-xs text-slate-600">
              {results
                .filter((r) => r.reason)
                .map((r, i) => (
                  <li key={i}>
                    <span className="font-mono">{r.id ?? `candidate_${i + 1}`}</span>: {r.reason}
                    {r.reason === "no_pae" &&
                      " — scores JSON에 PAE가 없어 인터페이스 지표를 계산할 수 없습니다."}
                  </li>
                ))}
            </ul>
          )}
          {results.some((r) => (r.warnings ?? []).length > 0) && (
            <ul className="mt-2 space-y-1 text-xs text-amber-700">
              {results.flatMap((r, i) =>
                (r.warnings ?? []).map((w, j) => <li key={`${i}-${j}`}>⚠ {w}</li>),
              )}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}

function FileField({
  label,
  hint,
  name,
  accept,
  onPick,
}: {
  label: string;
  hint: string;
  name: string;
  accept: string;
  onPick: (file: File | undefined) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-900">{label}</span>
      <input
        type="file"
        accept={accept}
        onChange={(e) => onPick(e.target.files?.[0])}
        className="mt-1 block w-full text-xs text-slate-600 file:mr-3 file:rounded file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-xs file:text-slate-700 hover:file:bg-slate-200"
      />
      <span className="mt-1 block text-xs text-slate-500">{name || hint}</span>
    </label>
  );
}

"use client";

import { useParams } from "next/navigation";
import useSWR from "swr";

import { getWorkflowRun, getWorkflowReport, WorkflowRunStep } from "@/lib/api";
import { JobStatusBadge } from "@/components/JobStatusBadge";

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

function formatMetrics(metrics: Record<string, unknown> | null): string {
  if (!metrics) return "";
  return Object.entries(metrics)
    .map(([key, value]) => `${key}: ${typeof value === "number" ? value : String(value)}`)
    .join(" · ");
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

export default function WorkflowRunPage() {
  // Same auth pattern as the rest of the app: no bearer token, SSO cookie only.
  const token = "";
  const params = useParams();
  const runId = Array.isArray(params.runId) ? params.runId[0] : params.runId;

  const { data, error } = useSWR(
    runId ? ["workflow-run", runId] : null,
    () => getWorkflowRun(token, runId as string),
    {
      refreshInterval: (latestData) =>
        latestData && TERMINAL_STATUSES.has(latestData.status) ? 0 : 5000,
    }
  );

  const { data: report } = useSWR(
    data && data.status === "completed" && runId ? ["workflow-report", runId] : null,
    () => getWorkflowReport(token, runId as string)
  );

  const steps: WorkflowRunStep[] = data ? [...data.steps].sort((a, b) => a.order - b.order) : [];

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-4xl px-6 py-10">
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-semibold text-slate-900">워크플로우 실행 결과</h1>
            {data && <JobStatusBadge status={data.status} />}
          </div>
          {error && (
            <p className="mt-3 text-sm text-rose-600">실행 정보를 불러오지 못했습니다.</p>
          )}
          {data?.error_message && (
            <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-600">
              {data.error_message}
            </p>
          )}
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-slate-900">단계</h2>
          {!data && <p className="mt-3 text-sm text-slate-500">불러오는 중...</p>}
          <ol className="mt-4 space-y-4">
            {steps.map((step) => (
              <li key={`${step.order}-${step.step_name}`} className="rounded-2xl border border-slate-100 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">
                      {step.order}. {step.step_name}
                    </p>
                    <p className="text-xs text-slate-500">{step.worker_name}</p>
                  </div>
                  <JobStatusBadge status={step.status} />
                </div>
                {step.metrics && (
                  <p className="mt-2 text-xs text-slate-600">{formatMetrics(step.metrics)}</p>
                )}
                {step.error_message && (
                  <p className="mt-2 text-sm text-rose-600">{step.error_message}</p>
                )}
                {step.logs && (
                  <details className="mt-2 text-xs text-slate-500">
                    <summary className="cursor-pointer font-semibold text-slate-600">로그</summary>
                    <pre className="mt-2 whitespace-pre-wrap rounded-xl bg-slate-50 p-3 font-mono">{step.logs}</pre>
                  </details>
                )}
              </li>
            ))}
          </ol>
        </div>

        {data?.status === "completed" && (
          <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-slate-900">리포트</h2>
            {!report && <p className="mt-3 text-sm text-slate-500">리포트를 불러오는 중...</p>}
            {report && report.candidates.length === 0 && (
              <p className="mt-3 text-sm text-slate-500">후보 서열이 없습니다.</p>
            )}
            {report && report.candidates.length > 0 && (
              <table className="mt-4 w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500">
                    <th className="py-2 pr-4 font-semibold">#</th>
                    <th className="py-2 pr-4 font-semibold">sequence</th>
                    <th className="py-2 pr-4 font-semibold">soluprot_score</th>
                    <th className="py-2 pr-4 font-semibold">plddt</th>
                  </tr>
                </thead>
                <tbody>
                  {report.candidates.map((candidate, index) => {
                    const sequence = typeof candidate.sequence === "string" ? candidate.sequence : "";
                    const soluprot = candidate.soluprot_score;
                    const plddt = candidate.plddt;
                    return (
                      <tr key={String(candidate.id ?? index)} className="border-b border-slate-100 last:border-0">
                        <td className="py-3 pr-4 text-slate-500">{index + 1}</td>
                        <td className="py-3 pr-4 font-mono text-xs text-slate-700">{truncate(sequence, 40)}</td>
                        <td className="py-3 pr-4 text-slate-700">
                          {typeof soluprot === "number" ? soluprot.toFixed(3) : soluprot != null ? String(soluprot) : "—"}
                        </td>
                        <td className="py-3 pr-4 text-slate-700">
                          {typeof plddt === "number" ? plddt.toFixed(1) : plddt != null ? String(plddt) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </section>
    </main>
  );
}

"use client";

import Link from "next/link";
import useSWR from "swr";

import { listWorkflows } from "@/lib/api";
import { JobStatusBadge } from "@/components/JobStatusBadge";

export default function WorkflowsPage() {
  // Identity travels via the gateway-injected header (Caddy forward_auth SSO);
  // the browser holds no bearer token, so pass an empty string like page.tsx does.
  const token = "";

  const { data, isLoading } = useSWR(["workflows"], () => listWorkflows(token));
  const workflows = data?.workflows ?? [];

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">워크플로우</h1>
          <Link
            href="/workflows/new"
            className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-white"
          >
            + RAPID 워크플로우 만들기
          </Link>
        </div>

        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
          {isLoading && <p className="text-sm text-slate-500">불러오는 중...</p>}
          {!isLoading && workflows.length === 0 && (
            <p className="text-sm text-slate-500">아직 워크플로우가 없습니다.</p>
          )}
          {workflows.length > 0 && (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-2 pr-4 font-semibold">이름</th>
                  <th className="py-2 pr-4 font-semibold">설명</th>
                  <th className="py-2 pr-4 font-semibold">최근 실행 상태</th>
                  <th className="py-2 pr-4 font-semibold">생성일</th>
                  <th className="py-2 pr-4 font-semibold" />
                </tr>
              </thead>
              <tbody>
                {workflows.map((workflow) => (
                  <tr key={workflow.id} className="border-b border-slate-100 last:border-0">
                    <td className="py-3 pr-4 font-medium text-slate-900">{workflow.name}</td>
                    <td className="py-3 pr-4 text-slate-500">{workflow.description || "—"}</td>
                    <td className="py-3 pr-4">
                      {workflow.last_run_status ? (
                        <JobStatusBadge status={workflow.last_run_status} />
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-slate-500">
                      {new Date(workflow.created_at).toLocaleString("ko-KR")}
                    </td>
                    <td className="py-3 pr-4 text-right">
                      <Link
                        href="/workflows/new"
                        className="rounded-full border border-brand-200 px-4 py-2 text-sm font-semibold text-brand-700"
                      >
                        실행하기
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </main>
  );
}

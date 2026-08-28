"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import {
  AdminActivityItem,
  AdminHistoryItem,
  AdminUsageRow,
  fetchAdminActivity,
  fetchAdminFlag,
  fetchAdminHistory,
  fetchAdminUsage,
} from "@/lib/api";

// Identity travels via the gateway-injected SSO header; the browser holds no
// bearer token (same pattern as the other pages).
const TOKEN = "";

const TABS = [
  { key: "activity", label: "현재 현황" },
  { key: "history", label: "전체 이력" },
  { key: "usage", label: "사용자별" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function duration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return `${seconds}초`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분`;
  return `${(seconds / 3600).toFixed(1)}시간`;
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString("ko-KR") : "-";
}

function ActivityTab() {
  // Only this tab polls: it is the one that goes stale on its own.
  const { data, isLoading } = useSWR(["admin-activity"], () => fetchAdminActivity(TOKEN), {
    refreshInterval: 5000,
  });
  const items: AdminActivityItem[] = data?.items ?? [];

  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중...</p>;
  if (items.length === 0)
    return <p className="text-sm text-slate-500">지금 실행 중이거나 대기 중인 작업이 없습니다.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-slate-500">
          <tr>
            <th className="py-2">사용자</th>
            <th>종류</th>
            <th>이름</th>
            <th>상태</th>
            <th>시작</th>
            <th>경과</th>
            <th>대기</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.kind}-${item.id}`} className="border-t border-slate-100">
              <td
                className="max-w-[16rem] truncate py-2 font-medium text-slate-700"
                title={item.owner}
              >
                {item.owner}
              </td>
              <td className="text-slate-500">{item.kind === "job" ? "작업" : "워크플로"}</td>
              <td className="text-slate-700">{item.name}</td>
              <td className="text-slate-600">{item.status}</td>
              <td className="text-slate-500">{when(item.started_at)}</td>
              <td className="text-slate-500">{duration(item.elapsed_seconds)}</td>
              <td className="text-slate-500">{item.queue_position ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistoryTab() {
  const [user, setUser] = useState("");
  const [status, setStatus] = useState("");
  const { data, isLoading } = useSWR(["admin-history", user, status], () =>
    fetchAdminHistory(TOKEN, { user: user || undefined, status: status || undefined }),
  );
  const items: AdminHistoryItem[] = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <input
          value={user}
          onChange={(event) => setUser(event.target.value)}
          placeholder="사용자 (이름/이메일 일부)"
          className="rounded-full border border-slate-200 px-4 py-2 text-sm"
        />
        <input
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          placeholder="상태 (completed/failed/...)"
          className="rounded-full border border-slate-200 px-4 py-2 text-sm"
        />
        <span className="self-center text-sm text-slate-500">총 {data?.total ?? 0}건</span>
      </div>
      {isLoading && <p className="text-sm text-slate-500">불러오는 중...</p>}
      {!isLoading && items.length === 0 && (
        <p className="text-sm text-slate-500">조건에 맞는 기록이 없습니다.</p>
      )}
      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2">사용자</th>
                <th>종류</th>
                <th>이름</th>
                <th>상태</th>
                <th>시각</th>
                <th>소요</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${item.kind}-${item.id}`} className="border-t border-slate-100">
                  <td
                    className="max-w-[16rem] truncate py-2 font-medium text-slate-700"
                    title={item.owner}
                  >
                    {item.owner}
                  </td>
                  <td className="text-slate-500">{item.kind === "job" ? "작업" : "워크플로"}</td>
                  <td className="text-slate-700">{item.name}</td>
                  <td className="text-slate-600">{item.status}</td>
                  <td className="text-slate-500">{when(item.created_at)}</td>
                  <td className="text-slate-500">{duration(item.duration_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function UsageTab() {
  const { data, isLoading } = useSWR(["admin-usage"], () => fetchAdminUsage(TOKEN));
  const rows: AdminUsageRow[] = data?.users ?? [];

  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중...</p>;
  if (rows.length === 0) return <p className="text-sm text-slate-500">집계할 기록이 없습니다.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-slate-500">
          <tr>
            <th className="py-2">사용자</th>
            <th>전체</th>
            <th>완료</th>
            <th>실패</th>
            <th>취소</th>
            <th>진행</th>
            <th>누적 시간</th>
            <th>마지막 활동</th>
          </tr>
        </thead>
        <tbody>
          {/* Keyed on owner_id, not the display label: two deleted accounts both
              render as "(unknown)" and two live accounts can share a name. */}
          {rows.map((row) => (
            <tr key={row.owner_id} className="border-t border-slate-100">
              <td
                className="max-w-[16rem] truncate py-2 font-medium text-slate-700"
                title={row.owner}
              >
                {row.owner}
              </td>
              <td className="text-slate-600">{row.total}</td>
              <td className="text-emerald-600">{row.completed}</td>
              <td className="text-rose-600">{row.failed}</td>
              <td className="text-slate-500">{row.cancelled}</td>
              <td className="text-slate-600">{row.active}</td>
              <td className="text-slate-500">{duration(row.total_seconds)}</td>
              <td className="text-slate-500">{when(row.last_activity)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AdminPage() {
  const { data: adminData, isLoading: adminLoading } = useSWR(["admin-flag"], () =>
    fetchAdminFlag(TOKEN),
  );
  const isAdmin = adminData?.is_admin === true;
  const [tab, setTab] = useState<TabKey>("activity");

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-7xl px-6 py-10">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">운영 현황</h1>
          <Link
            href="/"
            className="rounded-full border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-100"
          >
            ← 홈
          </Link>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          모든 사용자의 작업을 조회합니다. 관리자에게만 보입니다.
        </p>

        {adminLoading && <p className="mt-8 text-sm text-slate-500">확인 중...</p>}

        {!adminLoading && !isAdmin && (
          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
            이 페이지는 관리자만 접근할 수 있습니다. (Keycloak 실 역할{" "}
            <span className="font-mono">kbf-admin</span> 필요)
          </div>
        )}

        {isAdmin && (
          <div className="mt-6">
            <div className="flex gap-2">
              {TABS.map((entry) => (
                <button
                  key={entry.key}
                  onClick={() => setTab(entry.key)}
                  className={
                    tab === entry.key
                      ? "rounded-full bg-slate-900 px-5 py-2 text-sm text-white"
                      : "rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100"
                  }
                >
                  {entry.label}
                </button>
              ))}
            </div>
            <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
              {tab === "activity" && <ActivityTab />}
              {tab === "history" && <HistoryTab />}
              {tab === "usage" && <UsageTab />}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}

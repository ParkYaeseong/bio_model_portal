const palette: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-700",
  running: "bg-sky-100 text-sky-700",
  submitted: "bg-indigo-100 text-indigo-700",
  failed: "bg-rose-100 text-rose-700",
  pending: "bg-gray-100 text-gray-700",
};

export function JobStatusBadge({ status }: { status: string }) {
  const normalized = status?.toLowerCase();
  const tone = palette[normalized] || palette.pending;
  return <span className={`rounded-full px-3 py-1 text-xs font-semibold ${tone}`}>{status}</span>;
}


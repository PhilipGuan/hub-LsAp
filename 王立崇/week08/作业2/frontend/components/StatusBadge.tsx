"use client";

import type { Status } from "@/lib/api";

const MAP: Record<Status, { label: string; cls: string }> = {
  pending: { label: "排队中", cls: "bg-slate-100 text-slate-600" },
  running: { label: "研究中…", cls: "bg-blue-100 text-blue-700" },
  completed: { label: "已完成", cls: "bg-emerald-100 text-emerald-700" },
  failed: { label: "失败", cls: "bg-red-100 text-red-700" },
};

export default function StatusBadge({ status }: { status: Status }) {
  const m = MAP[status] ?? MAP.pending;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${m.cls}`}
    >
      {status === "running" && (
        <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
      )}
      {m.label}
    </span>
  );
}

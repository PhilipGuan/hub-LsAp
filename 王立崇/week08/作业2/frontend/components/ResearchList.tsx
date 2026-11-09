"use client";

import Link from "next/link";
import type { ResearchRecord } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import StatusBadge from "./StatusBadge";

export default function ResearchList({
  records,
  loading,
}: {
  records: ResearchRecord[];
  loading: boolean;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
        历史研究
      </h2>
      {loading ? (
        <p className="text-sm text-slate-400">加载中…</p>
      ) : records.length === 0 ? (
        <p className="text-sm text-slate-400">暂无历史研究，输入主题开始第一次研究吧。</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {records.map((r) => (
            <li key={r.research_id}>
              <Link
                href={`/research/${r.research_id}`}
                className="-mx-2 flex items-center justify-between gap-4 rounded px-2 py-3 hover:bg-slate-50"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-slate-800">{r.topic}</div>
                  <div className="mt-0.5 text-xs text-slate-400">
                    创建于 {fmtTime(r.created_at)}
                  </div>
                </div>
                <StatusBadge status={r.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

"use client";

import type { ResearchProcess } from "@/lib/api";
import { STEP_LABEL, stepDetail } from "@/lib/format";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-slate-50 px-4 py-3 text-center">
      <div className="text-2xl font-semibold text-slate-800">{value}</div>
      <div className="mt-0.5 text-xs text-slate-400">{label}</div>
    </div>
  );
}

export default function ProcessLog({ process }: { process: ResearchProcess | null }) {
  if (!process) return null;
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
        研究过程记录
      </h2>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="迭代轮数" value={process.iterations} />
        <Stat label="初始关键词" value={process.plan.length} />
        <Stat label="全部检索词" value={process.search_queries.length} />
        <Stat label="已读页面" value={process.reviewed_urls.length} />
      </div>

      {process.plan.length > 0 && (
        <div className="mt-5">
          <div className="mb-1.5 text-sm font-medium text-slate-500">初始规划关键词</div>
          <div className="flex flex-wrap gap-2">
            {process.plan.map((k, i) => (
              <span key={i} className="rounded-full bg-indigo-50 px-3 py-1 text-sm text-indigo-700">
                {k}
              </span>
            ))}
          </div>
        </div>
      )}

      {process.search_queries.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-sm font-medium text-slate-500">全部检索关键词</div>
          <div className="flex flex-wrap gap-2">
            {process.search_queries.map((k, i) => (
              <span key={i} className="rounded-full bg-slate-100 px-3 py-1 text-sm text-slate-600">
                {k}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-5">
        <div className="mb-2 text-sm font-medium text-slate-500">逐步记录</div>
        <ol className="space-y-2 border-l border-slate-200 pl-4">
          {process.steps.map((s, i) => (
            <li key={i} className="relative text-sm">
              <span className="absolute top-1.5 -left-[21px] h-2 w-2 rounded-full bg-indigo-400" />
              <span className="mr-2 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                {STEP_LABEL[s.type] ?? s.type}
                {s.round ? ` · 第${s.round}轮` : ""}
              </span>
              <span className="text-slate-600">{stepDetail(s)}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

"use client";

import type { ResearchRecord } from "@/lib/api";
import { stepDetail } from "@/lib/format";

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-white/70 px-4 py-3 text-center">
      <div className="text-2xl font-semibold text-blue-800">{value}</div>
      <div className="mt-0.5 text-xs text-blue-500">{label}</div>
    </div>
  );
}

export default function ProgressPanel({ record }: { record: ResearchRecord }) {
  const process = record.process;
  const latestSteps = process?.steps?.slice(-8) ?? [];

  return (
    <section className="rounded-xl border border-blue-200 bg-blue-50/60 p-6">
      <div className="mb-4 flex items-center gap-2">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-blue-500" />
        <h2 className="text-base font-semibold text-blue-800">
          研究中…（自动检索、阅读、迭代，请稍候）
        </h2>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <MiniStat label="当前轮次" value={process?.iterations ?? 0} />
        <MiniStat label="检索关键词" value={process?.search_queries?.length ?? 0} />
        <MiniStat label="正文段落" value={record.draft?.length ?? 0} />
        <MiniStat label="已收集来源" value={record.sources?.length ?? 0} />
      </div>

      <div className="mt-4">
        <div className="mb-2 text-sm font-medium text-blue-700">实时步骤</div>
        {latestSteps.length === 0 ? (
          <p className="text-sm text-slate-500">正在初始化研究流程…</p>
        ) : (
          <ul className="space-y-1 font-mono text-xs text-slate-700">
            {latestSteps.map((s, i) => (
              <li key={i}>
                <span className="text-blue-400">▸</span> {stepDetail(s)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

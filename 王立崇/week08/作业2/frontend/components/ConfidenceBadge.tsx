"use client";

import type { ConfidenceNote } from "@/lib/api";

const LEVEL: Record<string, { label: string; cls: string; dot: string }> = {
  high: { label: "高", cls: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  medium: { label: "中", cls: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  low: { label: "低", cls: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
};

export default function ConfidenceBadge({ confidence }: { confidence: ConfidenceNote | null }) {
  if (!confidence) return null;
  const lv = LEVEL[confidence.overall] ?? LEVEL.medium;
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <h2 className="mb-3 flex items-center gap-2 text-base font-semibold text-slate-700">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${lv.dot}`} />
        置信度说明
      </h2>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className={`rounded-full border px-3 py-1 font-medium ${lv.cls}`}>
          可靠程度：{lv.label}（{confidence.overall}）
        </span>
        {confidence.info_cutoff && (
          <span className="text-slate-500">信息截止：{confidence.info_cutoff}</span>
        )}
      </div>
      {confidence.notes.length > 0 && (
        <ul className="mt-3 list-inside list-disc space-y-1 text-sm text-slate-600">
          {confidence.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

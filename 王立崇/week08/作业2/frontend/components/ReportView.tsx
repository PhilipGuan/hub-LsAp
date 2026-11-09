"use client";

import type { Conclusion, ReportContent } from "@/lib/api";

function ConclusionItem({ c }: { c: Conclusion }) {
  return (
    <li className="text-sm leading-relaxed text-slate-700">
      <span className="mr-1 text-slate-400">·</span>
      {c.text}
      {c.is_model_inference && (
        <span className="ml-2 inline-flex items-center rounded bg-violet-100 px-1.5 py-0.5 text-xs font-medium text-violet-700">
          模型推断
        </span>
      )}
      {c.sources.length > 0 && (
        <span className="ml-2 text-xs text-slate-400">
          [{c.sources.map((s) => s.title || s.url).join("；")}]
        </span>
      )}
    </li>
  );
}

function ConclusionList({ items }: { items: Conclusion[] }) {
  if (!items.length) return null;
  return (
    <ul className="space-y-1.5">
      {items.map((c, i) => (
        <ConclusionItem key={i} c={c} />
      ))}
    </ul>
  );
}

export default function ReportView({ report }: { report: ReportContent | null }) {
  if (!report) return null;
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
        研究报告
      </h2>
      <h1 className="text-2xl font-bold text-slate-900">{report.title}</h1>
      {report.summary && (
        <p className="mt-3 leading-relaxed text-slate-600">{report.summary}</p>
      )}

      <div className="mt-6 space-y-6">
        {report.sections.map((s, i) => (
          <div key={i} className="border-t border-slate-100 pt-5">
            <h3 className="mb-2 text-lg font-semibold text-slate-800">{s.heading}</h3>
            <div className="whitespace-pre-wrap leading-relaxed text-slate-700">{s.body}</div>
            {s.conclusions.length > 0 && (
              <div className="mt-3">
                <div className="mb-1.5 text-sm font-medium text-slate-500">本节结论</div>
                <ConclusionList items={s.conclusions} />
              </div>
            )}
          </div>
        ))}
      </div>

      {report.key_conclusions.length > 0 && (
        <div className="mt-6 border-t border-slate-100 pt-5">
          <h3 className="mb-2 text-lg font-semibold text-slate-800">关键结论</h3>
          <ConclusionList items={report.key_conclusions} />
        </div>
      )}

      {report.open_questions.length > 0 && (
        <div className="mt-6 border-t border-slate-100 pt-5">
          <h3 className="mb-2 text-lg font-semibold text-slate-800">遗留问题</h3>
          <ul className="space-y-1.5 text-sm text-slate-700">
            {report.open_questions.map((q, i) => (
              <li key={i}>
                <span className="mr-1 text-slate-400">·</span>
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

"use client";

import type { Source } from "@/lib/api";

export default function SourcesList({ sources }: { sources: Source[] }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
        来源列表（{sources.length}）
      </h2>
      {sources.length === 0 ? (
        <p className="text-sm text-slate-400">暂无来源。</p>
      ) : (
        <ol className="space-y-3">
          {sources.map((s, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded bg-slate-100 text-xs font-medium text-slate-500">
                {i + 1}
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-indigo-600 break-all hover:underline"
                  >
                    {s.title || s.url}
                  </a>
                  {s.site_name && <span className="text-xs text-slate-400">{s.site_name}</span>}
                  {s.accessed_at && (
                    <span className="text-xs text-slate-400">访问 {s.accessed_at}</span>
                  )}
                </div>
                {s.snippet && (
                  <p className="mt-1 text-slate-500 line-clamp-2">{s.snippet}</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

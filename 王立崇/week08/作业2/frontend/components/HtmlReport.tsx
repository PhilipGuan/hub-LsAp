"use client";

import { useEffect, useState } from "react";

export default function HtmlReport({ html, title }: { html: string; title: string }) {
  const [open, setOpen] = useState(false);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!html) {
      setBlobUrl(null);
      return;
    }
    const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [html]);

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          完整 HTML 报告
        </h2>
        <div className="flex gap-2">
          <button
            onClick={() => setOpen((v) => !v)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            {open ? "收起预览" : "展开预览"}
          </button>
          {blobUrl && (
            <a
              href={blobUrl}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500"
            >
              在新窗口打开
            </a>
          )}
        </div>
      </div>

      {!html ? (
        <p className="text-sm text-slate-400">暂无 HTML 报告。</p>
      ) : open ? (
        <iframe
          srcDoc={html}
          title={title}
          sandbox="allow-scripts allow-popups"
          className="h-[70vh] w-full rounded-lg border border-slate-200 bg-white"
        />
      ) : (
        <p className="text-sm text-slate-400">
          点击「展开预览」内嵌查看，或「在新窗口打开」查看完整排版。
        </p>
      )}
    </section>
  );
}

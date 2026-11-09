"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useResearch } from "@/lib/useResearch";
import { fmtTime } from "@/lib/format";
import StatusBadge from "@/components/StatusBadge";
import ProgressPanel from "@/components/ProgressPanel";
import ConfidenceBadge from "@/components/ConfidenceBadge";
import ReportView from "@/components/ReportView";
import HtmlReport from "@/components/HtmlReport";
import SourcesList from "@/components/SourcesList";
import ProcessLog from "@/components/ProcessLog";

export default function ResearchDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { record, loading, error } = useResearch(id);

  if (loading && !record) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-slate-400">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-500" />
        <p className="text-sm">正在加载研究记录…</p>
      </div>
    );
  }

  if (error && !record) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          加载失败：{error}
        </div>
        <Link href="/" className="inline-block text-sm text-indigo-600 hover:underline">
          ← 返回首页
        </Link>
      </div>
    );
  }

  if (!record) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-slate-400">未找到该研究记录。</p>
        <Link href="/" className="inline-block text-sm text-indigo-600 hover:underline">
          ← 返回首页
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-1 text-sm text-slate-400">研究主题</div>
            <h1 className="text-xl font-semibold">{record.topic}</h1>
            <div className="mt-2 text-xs text-slate-400">
              ID: {record.research_id} · 创建于 {fmtTime(record.created_at)} · 更新于{" "}
              {fmtTime(record.updated_at)}
            </div>
          </div>
          <div className="shrink-0">
            <StatusBadge status={record.status} />
          </div>
        </div>
        <div className="mt-4">
          <Link href="/" className="text-sm text-indigo-600 hover:underline">
            ← 返回首页
          </Link>
        </div>
      </section>

      {(record.status === "pending" || record.status === "running") && (
        <ProgressPanel record={record} />
      )}

      {record.status === "failed" && (
        <section className="rounded-xl border border-red-200 bg-red-50 p-6">
          <div className="font-semibold text-red-700">研究失败</div>
          <p className="mt-1 text-sm text-red-600">{record.error || "未知错误"}</p>
        </section>
      )}

      {record.status === "completed" && (
        <>
          <ConfidenceBadge confidence={record.confidence} />
          <ReportView report={record.report} />
          <HtmlReport html={record.report_html} title={record.report?.title || record.topic} />
          <SourcesList sources={record.sources} />
          <ProcessLog process={record.process} />
        </>
      )}
    </div>
  );
}

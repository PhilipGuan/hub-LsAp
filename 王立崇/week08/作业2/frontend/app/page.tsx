"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  API_BASE_URL,
  checkHealth,
  listResearch,
  startResearch,
  type ResearchRecord,
} from "@/lib/api";
import TopicForm from "@/components/TopicForm";
import ResearchList from "@/components/ResearchList";

export default function HomePage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [records, setRecords] = useState<ResearchRecord[]>([]);
  const [loadingList, setLoadingList] = useState(true);

  const refreshList = useCallback(async () => {
    setLoadingList(true);
    try {
      setRecords(await listResearch());
    } catch {
      setRecords([]);
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    checkHealth().then(setHealthy);
    refreshList();
  }, [refreshList]);

  async function handleSubmit(topic: string) {
    setSubmitting(true);
    setError("");
    try {
      const { research_id } = await startResearch(topic);
      router.push(`/research/${research_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-slate-200 bg-white p-6">
        <h1 className="text-2xl font-bold">深度研究助手</h1>
        <p className="mt-2 leading-relaxed text-slate-600">
          输入一个研究主题（竞品分析、行业趋势、技术选型、政策解读…），工具自动完成检索、阅读、迭代、综合，产出一份带来源引用的研究报告。
        </p>
        <div className="mt-4 flex items-center gap-2 text-sm">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              healthy === true
                ? "bg-emerald-500"
                : healthy === false
                  ? "bg-red-500"
                  : "bg-slate-300"
            }`}
          />
          <span className="text-slate-500">
            后端：{API_BASE_URL}{" "}
            {healthy === true
              ? "· 已连接"
              : healthy === false
                ? "· 未连接（请确认后端已启动）"
                : "· 检测中…"}
          </span>
        </div>
      </section>

      <TopicForm onSubmit={handleSubmit} submitting={submitting} />

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <ResearchList records={records} loading={loadingList} />
    </div>
  );
}

"use client";

// 轮询单个研究记录：status 到达 completed / failed 后停止，否则每隔 POLL_INTERVAL 重查。
// 后端研究是后台执行（POST 立即返回，研究在事件循环里跑），前端靠此轮询实时看到中间结果。

import { useEffect, useState } from "react";
import { getResearch, type ResearchRecord } from "./api";

const POLL_INTERVAL = 2000; // 正常轮询间隔（ms）
const ERROR_RETRY = 3000; // 请求失败后的重试间隔（ms）

export function useResearch(id: string) {
  const [record, setRecord] = useState<ResearchRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const rec = await getResearch(id);
        if (stopped) return;
        setRecord(rec);
        setError(null);
        setLoading(false);
        if (rec.status === "completed" || rec.status === "failed") return; // 停止轮询
        timer = setTimeout(tick, POLL_INTERVAL);
      } catch (err) {
        if (stopped) return;
        setError(err instanceof Error ? err.message : String(err));
        timer = setTimeout(tick, ERROR_RETRY);
      }
    }

    tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  return { record, loading, error };
}

// 研究过程记录的展示格式化（与 backend/engine.py 里 steps 的 detail 结构对齐）。

import type { ProcessStep } from "./api";

export const STEP_LABEL: Record<string, string> = {
  plan: "规划",
  search: "检索",
  summarize: "总结",
  judge: "判断补检",
};

export function stepDetail(step: ProcessStep): string {
  const { type, detail } = step;
  switch (type) {
    case "plan":
      return `生成初始关键词：${(detail.keywords as string[])?.join("、") ?? ""}`;
    case "search":
      return `检索「${detail.keyword}」→ ${detail.results} 条结果`;
    case "summarize":
      return `总结「${detail.keyword}」→ ${detail.chars} 字正文`;
    case "judge":
      return detail.sufficient
        ? "判断：信息已足够，结束检索"
        : `判断：信息不足，补充关键词：${(detail.new_keywords as string[])?.join("、") ?? "无"}`;
    default:
      return JSON.stringify(detail);
  }
}

export function fmtTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN");
}

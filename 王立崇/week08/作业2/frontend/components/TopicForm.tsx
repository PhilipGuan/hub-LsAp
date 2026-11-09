"use client";

import { useState } from "react";

export default function TopicForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (topic: string) => void;
  submitting: boolean;
}) {
  const [topic, setTopic] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const t = topic.trim();
    if (!t || submitting) return;
    onSubmit(t);
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-xl border border-slate-200 bg-white p-6">
      <label htmlFor="topic" className="mb-2 block text-sm font-medium text-slate-600">
        输入研究主题
      </label>
      <div className="flex gap-3">
        <input
          id="topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="例如：2026 年主流 Agent 框架对比"
          maxLength={500}
          disabled={submitting}
          className="flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 focus:outline-none"
        />
        <button
          type="submit"
          disabled={submitting || !topic.trim()}
          className="rounded-lg bg-indigo-600 px-6 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "发起中…" : "开始研究"}
        </button>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        工具会自动检索、阅读、迭代、综合，产出一份带来源引用的研究报告（约需数十秒到几分钟）。
      </p>
    </form>
  );
}

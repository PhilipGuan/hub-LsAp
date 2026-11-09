import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "深度研究助手",
  description: "输入主题，自动检索、阅读、迭代、综合，产出一份带来源引用的研究报告。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
            <Link href="/" className="flex items-center gap-2 text-lg font-semibold">
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white">
                研
              </span>
              深度研究助手
            </Link>
            <span className="hidden text-sm text-slate-400 sm:block">
              自动检索 · 迭代 · 带来源引用的研究报告
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}

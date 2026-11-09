// 与 backend/models.py 逐字段对齐的类型定义 + API 客户端。
// 后端接口见 backend/app.py：
//   POST /api/research         发起一次研究（202，返回 research_id）
//   GET  /api/research/{rid}   查询研究状态 / 结果（轮询用）
//   GET  /api/research         研究列表
//   GET  /health               健康检查

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export type Status = "pending" | "running" | "completed" | "failed";

export interface SourceRef {
  url: string;
  title: string;
}

export interface Conclusion {
  text: string;
  sources: SourceRef[];
  // 无任何来源支撑、属于「模型推断」的结论
  is_model_inference: boolean;
}

export interface Section {
  heading: string;
  body: string;
  conclusions: Conclusion[];
}

export interface ConfidenceNote {
  overall: string; // high / medium / low
  info_cutoff: string; // 信息截止时间
  notes: string[];
}

export interface ReportContent {
  title: string;
  summary: string;
  sections: Section[];
  key_conclusions: Conclusion[];
  open_questions: string[];
}

export interface Source {
  url: string;
  title: string;
  site_name: string;
  snippet: string;
  accessed_at: string;
}

export interface ProcessStep {
  type: string; // plan / search / summarize / judge
  round: number;
  detail: Record<string, unknown>;
}

export interface ResearchProcess {
  plan: string[]; // 初始关键词
  search_queries: string[]; // 全部检索关键词
  reviewed_urls: string[]; // 实际使用的来源 URL
  iterations: number; // 检索/判断轮数
  steps: ProcessStep[];
}

export interface DraftBlock {
  round: number;
  keyword: string;
  text: string;
}

export interface ResearchRecord {
  research_id: string;
  topic: string;
  status: Status;
  created_at: string;
  updated_at: string;
  error: string | null;
  report: ReportContent | null;
  report_html: string;
  sources: Source[];
  draft: DraftBlock[];
  process: ResearchProcess | null;
  confidence: ConfidenceNote | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // 忽略非 JSON 错误体
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function startResearch(
  topic: string,
): Promise<{ research_id: string; status: string }> {
  return request("/api/research", {
    method: "POST",
    body: JSON.stringify({ topic }),
  });
}

export function getResearch(id: string): Promise<ResearchRecord> {
  return request(`/api/research/${id}`);
}

export function listResearch(): Promise<ResearchRecord[]> {
  return request("/api/research");
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

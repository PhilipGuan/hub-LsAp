// 后端 API 客户端。基址同源，走 Vite 代理 /api。
import type {
  AnalyzeResponse,
  HealthResponse,
  RecomputeRequest,
} from './types'

const BASE = '/api'

async function readError(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json()
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
      return JSON.stringify(detail)
    }
    return JSON.stringify(data)
  } catch {
    return `${response.status} ${response.statusText}`.trim()
  }
}

export async function analyze(form: FormData): Promise<AnalyzeResponse> {
  const response = await fetch(`${BASE}/analyze`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as AnalyzeResponse
}

export async function recompute(body: RecomputeRequest): Promise<AnalyzeResponse> {
  const response = await fetch(`${BASE}/recompute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as AnalyzeResponse
}

export async function health(): Promise<HealthResponse> {
  const response = await fetch(`${BASE}/health`)
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as HealthResponse
}

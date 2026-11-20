export type DocStatus = "pending" | "parsing" | "completed" | "failed";

export interface Doc {
  id: string;
  filename: string;
  size: number;
  sha256: string;
  page_count: number;
  status: DocStatus;
  attempts: number;
  error: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
  parsed_at: string | null;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  images_json: string;
  sources_json: string;
  created_at: string;
}

export interface Source {
  filename: string;
  page: number;
  doc_id: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const listDocs = async () => json<Doc[]>(await fetch("/api/documents"));

export async function uploadDocs(files: File[]): Promise<Doc[]> {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  return json<Doc[]>(await fetch("/api/documents", { method: "POST", body: fd }));
}

export const reparseDoc = async (id: string) =>
  json<Doc>(await fetch(`/api/documents/${id}/reparse`, { method: "POST" }));

export const deleteDoc = async (id: string) =>
  json<{ ok: boolean }>(await fetch(`/api/documents/${id}`, { method: "DELETE" }));

export const listSessions = async () => json<Session[]>(await fetch("/api/sessions"));

export const deleteSession = async (id: string) =>
  json<{ ok: boolean }>(await fetch(`/api/sessions/${id}`, { method: "DELETE" }));

export const getMessages = async (id: string) =>
  json<Message[]>(await fetch(`/api/sessions/${id}/messages`));

export interface ChatHandlers {
  onSession?: (id: string, userMessageId: string) => void;
  onSources?: (sources: Source[]) => void;
  onToken?: (token: string) => void;
  onDone?: (payload: { message_id: string; content: string; sources: Source[] }) => void;
  onError?: (message: string) => void;
}

function dispatch(block: string, handlers: ChatHandlers) {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  const raw = dataLines.join("\n");
  if (!raw) return;
  let data: any;
  try {
    data = JSON.parse(raw);
  } catch {
    return;
  }
  if (event === "session") handlers.onSession?.(data.session_id, data.user_message_id);
  else if (event === "sources") handlers.onSources?.(data.sources ?? []);
  else if (event === "token") handlers.onToken?.(data.t ?? "");
  else if (event === "done")
    handlers.onDone?.({
      message_id: data.message_id,
      content: data.content ?? "",
      sources: data.sources ?? [],
    });
  else if (event === "error") handlers.onError?.(data.message ?? "未知错误");
}

export async function streamChat(form: FormData, handlers: ChatHandlers): Promise<void> {
  const res = await fetch("/api/chat", { method: "POST", body: form });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx = buffer.indexOf("\n\n");
    while (idx >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (block.trim()) dispatch(block, handlers);
      idx = buffer.indexOf("\n\n");
    }
  }
}

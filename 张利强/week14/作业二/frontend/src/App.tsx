import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "./api";

const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: "待解析", cls: "badge-pending" },
  parsing: { label: "解析中", cls: "badge-parsing" },
  completed: { label: "解析完成", cls: "badge-completed" },
  failed: { label: "解析失败", cls: "badge-failed" },
};

interface UIMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  images?: string[];
  sources?: api.Source[];
  pending?: boolean;
  error?: string;
}

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(s: string) {
  const d = new Date(s.endsWith("Z") ? s : `${s}Z`);
  return d.toLocaleString("zh-CN", { hour12: false });
}

export default function App() {
  const [docs, setDocs] = useState<api.Doc[]>([]);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const [sessions, setSessions] = useState<api.Session[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [sending, setSending] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refreshDocs = useCallback(async () => {
    try {
      setDocs(await api.listDocs());
    } catch {
      /* 服务未就绪时静默 */
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refreshDocs();
    refreshSessions();
    const timer = window.setInterval(refreshDocs, 8000);

    const es = new EventSource("/api/events");
    es.addEventListener("doc_status", (ev) => {
      const p = JSON.parse((ev as MessageEvent).data);
      setDocs((prev) =>
        prev.map((d) =>
          d.id === p.doc_id
            ? {
                ...d,
                status: p.status,
                error: p.error ?? d.error,
                chunk_count: p.chunk_count ?? d.chunk_count,
                page_count: p.page_count ?? d.page_count,
              }
            : d
        )
      );
    });
    return () => {
      window.clearInterval(timer);
      es.close();
    };
  }, [refreshDocs, refreshSessions]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const updateLastAssistant = (fn: (m: UIMessage) => UIMessage) => {
    setMessages((prev) => {
      const copy = [...prev];
      const idx = copy.length - 1;
      if (idx >= 0 && copy[idx].role === "assistant") copy[idx] = fn(copy[idx]);
      return copy;
    });
  };

  async function handleUpload(list: FileList | null) {
    if (!list || list.length === 0) return;
    setUploading(true);
    setNotice(null);
    try {
      const created = await api.uploadDocs(Array.from(list));
      setDocs((prev) => {
        const map = new Map(prev.map((d) => [d.id, d]));
        created.forEach((d) => map.set(d.id, d));
        return Array.from(map.values());
      });
      setNotice({
        type: "ok",
        text: `已提交 ${created.length} 个文件，解析任务已进入 Kafka 队列`,
      });
    } catch (e: any) {
      setNotice({ type: "err", text: e.message ?? String(e) });
    } finally {
      setUploading(false);
    }
  }

  async function handleReparse(doc: api.Doc) {
    try {
      const updated = await api.reparseDoc(doc.id);
      setDocs((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    } catch (e: any) {
      setNotice({ type: "err", text: e.message ?? String(e) });
    }
  }

  async function handleDelete(doc: api.Doc) {
    if (!window.confirm(`删除《${doc.filename}》及其解析结果？`)) return;
    try {
      await api.deleteDoc(doc.id);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (e: any) {
      setNotice({ type: "err", text: e.message ?? String(e) });
    }
  }

  function attachFiles(list: FileList | null) {
    if (!list) return;
    const next = [...files, ...Array.from(list)].slice(0, 4);
    setFiles(next);
    setPreviews(next.map((f) => URL.createObjectURL(f)));
  }

  function removeAttachment(idx: number) {
    const next = files.filter((_, i) => i !== idx);
    setFiles(next);
    setPreviews(next.map((f) => URL.createObjectURL(f)));
  }

  async function openSession(id: string) {
    setSessionId(id);
    try {
      const msgs = await api.getMessages(id);
      setMessages(
        msgs.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          images: JSON.parse(m.images_json || "[]"),
          sources: JSON.parse(m.sources_json || "[]"),
        }))
      );
    } catch (e: any) {
      setNotice({ type: "err", text: e.message ?? String(e) });
    }
  }

  function newSession() {
    setSessionId(null);
    setMessages([]);
    setInput("");
  }

  async function handleDeleteSession(id: string) {
    if (!window.confirm("删除该会话？")) return;
    await api.deleteSession(id);
    if (sessionId === id) newSession();
    refreshSessions();
  }

  async function send() {
    const text = input.trim();
    if ((!text && files.length === 0) || sending) return;

    const fd = new FormData();
    fd.append("text", text);
    if (sessionId) fd.append("session_id", sessionId);
    files.forEach((f) => fd.append("images", f));

    const previewList = [...previews];
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, images: previewList },
      { role: "assistant", content: "", sources: [], pending: true },
    ]);
    setInput("");
    setFiles([]);
    setPreviews([]);
    setSending(true);

    try {
      await api.streamChat(fd, {
        onSession: (id) => {
          setSessionId(id);
          refreshSessions();
        },
        onSources: (sources) => updateLastAssistant((m) => ({ ...m, sources })),
        onToken: (t) =>
          updateLastAssistant((m) => ({ ...m, content: m.content + t, pending: false })),
        onDone: (p) =>
          updateLastAssistant((m) => ({
            ...m,
            content: p.content,
            sources: p.sources,
            pending: false,
          })),
        onError: (msg) => updateLastAssistant((m) => ({ ...m, error: msg, pending: false })),
      });
    } catch (e: any) {
      updateLastAssistant((m) => ({ ...m, error: e.message ?? String(e), pending: false }));
    } finally {
      setSending(false);
      refreshSessions();
    }
  }

  const currentTitle = sessions.find((s) => s.id === sessionId)?.title ?? "新会话";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-title">多模态 RAG 问答</div>
          <div className="brand-sub">Kafka · MinerU · Elasticsearch · GLM-4V</div>
        </div>

        <section className="panel">
          <div className="panel-head">
            <span>文档库</span>
            <button className="ghost" onClick={refreshDocs} title="刷新">
              ↻
            </button>
          </div>

          <label
            className={`dropzone ${uploading ? "busy" : ""}`}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              handleUpload(e.dataTransfer.files);
            }}
          >
            <input
              type="file"
              multiple
              accept="application/pdf"
              onChange={(e) => {
                handleUpload(e.target.files);
                e.currentTarget.value = "";
              }}
            />
            <span className="drop-icon">PDF</span>
            <span>{uploading ? "上传中…" : "点击或拖拽多个 PDF 到这里"}</span>
            <small>单个文件 ≤ 50MB</small>
          </label>

          {notice && <div className={`notice ${notice.type}`}>{notice.text}</div>}

          <ul className="doc-list">
            {docs.map((doc) => {
              const meta = STATUS_META[doc.status] ?? STATUS_META.pending;
              return (
                <li key={doc.id} className="doc-item">
                  <div className="doc-row">
                    <span className="doc-name" title={doc.filename}>
                      {doc.filename}
                    </span>
                    <span className={`badge ${meta.cls}`}>
                      {doc.status === "parsing" && <i className="dot" />}
                      {meta.label}
                    </span>
                  </div>
                  <div className="doc-meta">
                    <span>{fmtSize(doc.size)}</span>
                    {doc.page_count > 0 && <span>{doc.page_count} 页</span>}
                    {doc.status === "completed" && <span>{doc.chunk_count} 块</span>}
                    <span>{fmtTime(doc.created_at)}</span>
                  </div>
                  {doc.error && <div className="doc-error">{doc.error}</div>}
                  <div className="doc-actions">
                    {(doc.status === "failed" || doc.status === "completed") && (
                      <button className="ghost" onClick={() => handleReparse(doc)}>
                        重新解析
                      </button>
                    )}
                    <button className="ghost danger" onClick={() => handleDelete(doc)}>
                      删除
                    </button>
                  </div>
                </li>
              );
            })}
            {docs.length === 0 && <li className="empty">还没有文档，先上传一个 PDF</li>}
          </ul>
        </section>

        <section className="panel sessions">
          <div className="panel-head">
            <span>会话</span>
            <button className="ghost" onClick={newSession}>
              ＋ 新会话
            </button>
          </div>
          <ul className="session-list">
            {sessions.map((s) => (
              <li key={s.id} className={s.id === sessionId ? "active" : ""}>
                <button className="session-open" onClick={() => openSession(s.id)}>
                  {s.title}
                </button>
                <button className="ghost danger" onClick={() => handleDeleteSession(s.id)}>
                  ×
                </button>
              </li>
            ))}
            {sessions.length === 0 && <li className="empty">暂无历史会话</li>}
          </ul>
        </section>
      </aside>

      <main className="chat">
        <header className="chat-head">
          <div>
            <div className="chat-title">{currentTitle}</div>
            <div className="chat-sub">支持文字提问、图片提问与图文混排回答</div>
          </div>
        </header>

        <div className="messages" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="welcome">
              <h2>开始基于文档的问答</h2>
              <p>先在左侧上传 PDF，等状态变为「解析完成」，然后在这里提问。</p>
              <p className="hint">可以只发文字，也可以附上一张图片一起问。</p>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={m.id ?? i} className={`msg ${m.role}`}>
              <div className="avatar">{m.role === "user" ? "我" : "AI"}</div>
              <div className="bubble">
                {m.images && m.images.length > 0 && (
                  <div className="msg-images">
                    {m.images.map((src, j) => (
                      <img key={j} src={src} alt="上传图片" />
                    ))}
                  </div>
                )}
                {m.role === "assistant" ? (
                  m.content ? (
                    <div className="markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                    </div>
                  ) : (
                    m.pending && <div className="typing">正在检索并生成回答…</div>
                  )
                ) : (
                  <div className="plain">{m.content}</div>
                )}
                {m.error && <div className="msg-error">{m.error}</div>}
                {m.role === "assistant" && m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    <span className="sources-label">依据</span>
                    {m.sources.map((s, j) => (
                      <span key={j} className="source-chip">
                        {s.filename} · 第 {s.page} 页
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <footer className="composer">
          {previews.length > 0 && (
            <div className="attachments">
              {previews.map((src, i) => (
                <div key={i} className="attachment">
                  <img src={src} alt="待发送图片" />
                  <button onClick={() => removeAttachment(i)}>×</button>
                </div>
              ))}
            </div>
          )}
          <div className="composer-row">
            <button
              className="icon-btn"
              title="添加图片"
              onClick={() => fileInputRef.current?.click()}
            >
              🖼
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              multiple
              hidden
              onChange={(e) => {
                attachFiles(e.target.files);
                e.currentTarget.value = "";
              }}
            />
            <textarea
              value={input}
              placeholder="输入问题，Enter 发送，Shift+Enter 换行"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={1}
            />
            <button className="send" onClick={send} disabled={sending}>
              {sending ? "生成中…" : "发送"}
            </button>
          </div>
        </footer>
      </main>
    </div>
  );
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) {
        message =
          typeof body.detail === "string"
            ? body.detail
            : body.detail.map?.((item) => item.msg).join("；") || message;
      }
    } catch {
      // 非 JSON 错误响应，保留状态码信息。
    }
    throw new Error(message);
  }

  return response.status === 204 ? null : response.json();
}

export function health() {
  return request("/api/health");
}

export function chat(question, sessionId) {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify({
      question,
      session_id: sessionId || undefined,
    }),
  });
}

export function getSession(sessionId) {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`);
}

export function deleteSession(sessionId) {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

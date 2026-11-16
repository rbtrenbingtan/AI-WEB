export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (init.signal?.aborted) abort();
  if (!init.signal?.aborted) init.signal?.addEventListener("abort", abort);
  const timeout = setTimeout(
    abort,
    init.body instanceof FormData ? 120_000 : 30_000,
  );
  try {
    const response = await fetch(`/api${path}`, {
      ...init,
      headers,
      signal: controller.signal,
      credentials: "same-origin",
    }).catch(() => {
      throw new ApiError(
        controller.signal.aborted
          ? "请求超时，请重试。"
          : "暂时无法连接服务，请稍后重试。",
      );
    });
    const data: unknown = await response.json().catch(() => {
      throw new ApiError(
        `无法读取响应（${response.status}）。`,
        response.status,
      );
    });
    if (response.ok) return data as T;
    const detail = (data as { detail?: unknown }).detail;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "请检查选项与必填项。"
          : `请求未完成（${response.status}）。`,
      response.status,
    );
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener("abort", abort);
  }
}

export function post<T>(path: string, body: unknown = {}) {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

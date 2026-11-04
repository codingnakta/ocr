/* Thin fetch wrapper. All calls go to /api on the same origin (proxied by Next.js
 * to FastAPI). No secrets live in the browser: auth is an HttpOnly cookie. */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const CSRF = { "X-PB-CSRF": "1" };

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }
  if (!res.ok) {
    let msg = `요청 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  return handle<T>(await fetch(path, { credentials: "same-origin", cache: "no-store", ...init }));
}

export async function apiSend<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? CSRF : { ...CSRF, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return handle<T>(res);
}

/** Multipart upload with progress (XHR: fetch has no upload progress events). */
export function apiUpload<T>(path: string, form: FormData, onProgress?: (loaded: number, total: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-PB-CSRF", "1");
    xhr.upload.onprogress = (e) => onProgress?.(e.loaded, e.total);
    xhr.onerror = () => reject(new ApiError(0, "네트워크 오류로 업로드하지 못했습니다."));
    xhr.onload = () => {
      let body: { detail?: string } | null = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new ApiError(xhr.status, body?.detail ?? `업로드 실패 (${xhr.status})`));
    };
    xhr.send(form);
  });
}

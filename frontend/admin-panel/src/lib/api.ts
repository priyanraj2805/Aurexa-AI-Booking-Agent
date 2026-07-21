/**
 * Central API client for the DotStark backend.
 * Auth is via HttpOnly cookie (credentials: include).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";

type FetchOptions = RequestInit & {
  skipAuth?: boolean;
};

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  const { skipAuth = false, headers: customHeaders, ...rest } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest", // CSRF protection
    ...(customHeaders as Record<string, string>),
  };

  const response = await fetch(`${API_BASE}${path}`, {
    headers,
    credentials: "include", // Send HttpOnly cookies
    ...rest,
  });

  if (response.status === 401) {
    // Only redirect to login if not already there (prevents infinite reload loop)
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login") && !window.location.pathname.startsWith("/register")) {
      window.dispatchEvent(new Event("auth:unauthorized"));
    }
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    const errBody = await response.json().catch(() => ({}));
    // FastAPI validation errors return detail as array; extract first message
    const detail = errBody.detail;
    let message: string;
    if (Array.isArray(detail)) {
      message = detail.map((d: { msg?: string }) => d.msg || "Validation error").join(", ");
    } else if (typeof detail === "string") {
      message = detail;
    } else {
      message = `API Error: ${response.status}`;
    }
    throw new Error(message);
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

// ── Convenience helpers ──────────────────────────

export const api = {
  get: <T = unknown>(path: string) => apiFetch<T>(path, { method: "GET" }),
  post: <T = unknown>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T = unknown>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  del: <T = unknown>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
};

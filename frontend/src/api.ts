export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

function selectedWorkspacePath(path: string): string {
  const workspace = new URLSearchParams(window.location.search).get("workspace");
  if (!workspace || !path.startsWith("/api/")) return path;
  const url = new URL(path, window.location.origin);
  if (!url.searchParams.has("workspace")) url.searchParams.set("workspace", workspace);
  return `${url.pathname}${url.search}${url.hash}`;
}

function errorMessage(payload: unknown, status: number): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    const nested = record.error && typeof record.error === "object" ? record.error as Record<string, unknown> : undefined;
    for (const value of [record.detail, record.message, nested?.message, nested?.code]) {
      if (typeof value === "string" && value.trim()) return value;
    }
  }
  if (status === 401) return "Sign in through Django Admin to make changes.";
  if (status === 403) return "This signed-in principal is not allowed to perform that action.";
  return `Request failed (${status}).`;
}

export async function requestJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = String(init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(selectedWorkspacePath(path), {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new ApiError(response.ok ? "The service returned an unexpected response." : errorMessage(null, response.status), response.status, null);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError("The service returned invalid JSON.", response.status, null);
  }
  if (!response.ok) throw new ApiError(errorMessage(payload, response.status), response.status, payload);
  if (payload && typeof payload === "object" && (payload as Record<string, unknown>).ok === false) {
    throw new ApiError(errorMessage(payload, response.status), response.status, payload);
  }
  return payload as T;
}

export function apiErrorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "The request could not be completed.";
}

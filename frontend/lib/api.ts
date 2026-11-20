/**
 * API client.
 *
 * Version: 2.0.0 (2026-11-20)
 *
 * Added in 2.0.0:
 *  - `newIdempotencyKey()` and `postIdempotent()`. Every mutating auction call
 *    carries a client-generated key, so a retry, a double-tap or a reconnect
 *    cannot place a second bid or sell a player twice. This matters because two
 *    operator consoles drive one auction (auctioneer plus assistant).
 *  - `describeError()`, which turns a FastAPI error into something an operator
 *    can act on, and separates 409 (someone else got there first — re-read the
 *    price) from 400 (your bid broke a rule).
 */
import axios, { AxiosError, AxiosRequestConfig } from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export const api = axios.create({
  baseURL: API_URL,
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    // Only auto-redirect on 401 if a token exists (expired session).
    // Don't redirect during login/godmode/magic-code — those 401s are expected
    // and handled locally by each form.
    const authEndpoints = [
      "/auth/login",
      "/auth/godmode",
      "/auth/verify-magic-code",
      "/auth/send-magic-code",
    ];
    const url: string = err.config?.url ?? "";
    const isAuthCall = authEndpoints.some((e) => url.includes(e));

    if (
      err.response?.status === 401 &&
      typeof window !== "undefined" &&
      !isAuthCall &&
      localStorage.getItem("token")
    ) {
      localStorage.removeItem("token");
      window.location.href = "/auth/login";
    }
    return Promise.reject(err);
  }
);

/** A key that identifies one logical operation across retries. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/**
 * POST a mutating auction action with an idempotency key in the body.
 * Pass the same `key` when retrying the same logical action.
 */
export async function postIdempotent<T = any>(
  url: string,
  body: Record<string, unknown> = {},
  key?: string,
  config?: AxiosRequestConfig
) {
  const idempotency_key = key ?? newIdempotencyKey();
  return api.post<T>(
    url,
    { ...body, idempotency_key },
    {
      ...config,
      headers: { ...(config?.headers ?? {}), "Idempotency-Key": idempotency_key },
    }
  );
}

export interface ApiErrorInfo {
  message: string;
  /** True when another operator's action landed first and the price has moved. */
  conflict: boolean;
  status?: number;
}

export function describeError(err: unknown): ApiErrorInfo {
  const axiosErr = err as AxiosError<{ detail?: unknown }>;
  const status = axiosErr?.response?.status;
  const detail = axiosErr?.response?.data?.detail;

  let message: string;
  if (typeof detail === "string") {
    message = detail;
  } else if (Array.isArray(detail)) {
    // FastAPI validation errors arrive as a list of objects.
    message = detail
      .map((d: any) => d?.msg)
      .filter(Boolean)
      .join("; ");
  } else if (axiosErr?.message === "Network Error") {
    message = "Cannot reach the server. Check the connection and try again.";
  } else {
    message = axiosErr?.message || "Something went wrong";
  }

  return { message, conflict: status === 409, status };
}

export default api;

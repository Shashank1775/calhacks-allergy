/**
 * One fetch wrapper for the orders and vision HTTP APIs.
 *
 * Rules: fail closed. It never throws to a screen: a network error, a missing
 * URL or an error body all come back as `ok: false` with a code, so the screen
 * shows the problem instead of guessing a result.
 */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; code: string; message: string };

const MOCK_ORDERS_URL = "http://orders.mock";
const MOCK_VISION_URL = "http://vision.mock";

/** Browser mocks run only in a dev build that asks for them (web-framework decision). */
export function mocks_enabled(): boolean {
  return import.meta.env.DEV && import.meta.env.WEB_MOCK_API === "1";
}

export function orders_url(): string | undefined {
  return import.meta.env.WEB_ORDERS_URL || (mocks_enabled() ? MOCK_ORDERS_URL : undefined);
}

export function vision_url(): string | undefined {
  return import.meta.env.WEB_VISION_URL || (mocks_enabled() ? MOCK_VISION_URL : undefined);
}

export async function call_api<T>(
  base_url: string | undefined,
  path: string,
  method: "POST",
  body?: unknown,
): Promise<ApiResult<T>> {
  if (!base_url) {
    return { ok: false, status: 0, code: "not_configured", message: "The service URL isn't set." };
  }

  let response: Response;
  try {
    response = await fetch(base_url + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    return { ok: false, status: 0, code: "network_error", message: "Couldn't reach the service." };
  }

  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const error = json?.error;
    return {
      ok: false,
      status: response.status,
      code: error?.code ?? `http_${response.status}`,
      message: error?.message ?? response.statusText,
    };
  }
  return { ok: true, data: json as T };
}

import { validOutcome } from "./render.js";

export class APIError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export async function fetchJSON(
  fetcher,
  path,
  { timeout = 15000, signal, ...options } = {},
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    const response = await fetcher(path, {
      ...options,
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    let body;
    try {
      body = await response.json();
    } catch {
      throw new APIError(
        "invalid_response",
        "The service returned an unreadable response. No answer was displayed.",
      );
    }
    return {
      body,
      ok: response.ok,
      status: response.status,
      requestId: response.headers.get("X-Request-ID"),
    };
  } catch (error) {
    if (error instanceof APIError) throw error;
    if (controller.signal.aborted) {
      if (signal?.aborted)
        throw new APIError(
          "cancelled",
          "Stopped waiting. No answer was displayed.",
        );
      throw new APIError(
        "request_timeout",
        "The request took too long. No answer was displayed.",
      );
    }
    throw new APIError(
      "network_error",
      "Cannot reach the service. Check that the local server is running, then try again.",
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export async function askQuestion(fetcher, payload, options) {
  const response = await fetchJSON(fetcher, "/chat", {
    ...options,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const searched = response.body?.searched_documents;
  const selection =
    payload.scope?.mode === "selected" ? payload.scope.document_ids : null;
  const consistentScope =
    !selection ||
    !searched?.length ||
    (Array.isArray(searched) &&
      Array.isArray(selection) &&
      searched.length === new Set(selection).size &&
      searched.every((id) => selection.includes(id)));
  if (
    !validOutcome(response.body, response.ok) ||
    response.body.request_id !== response.requestId ||
    !consistentScope
  ) {
    throw new APIError(
      "invalid_response",
      "The service returned an incomplete or inconsistent response. No answer was displayed.",
    );
  }
  return response.body;
}

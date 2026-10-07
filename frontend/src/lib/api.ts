/**
 * Every call to the backend goes through the allow-listed proxy at /api/backend (PLAN 6.6), so the
 * browser never needs the backend's address and no CORS setup. A non-2xx answer carries the error
 * envelope of PLAN 5.7 and becomes an `ApiError`; so does a network failure or a body that is
 * not JSON, with a code of this file's own.
 */
import { isErrorEnvelope, isQueryResponse, isRecord } from "./guards";
import type { AnalysisRequest, QueryRequest, QueryResponse } from "./types";

const BASE = "/api/backend";

export class ApiError extends Error {
  /** A backend code (`invalid_request`, `planner_unavailable`, ...), the proxy's `backend_unreachable`, or `network_error`, `http_error`, `invalid_response`. */
  readonly code: string;
  /** The HTTP status; 0 when no answer arrived. */
  readonly status: number;
  readonly requestId: string | null;
  readonly isRetryable: boolean;
  readonly details: Record<string, unknown>;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    requestId?: string | null;
    isRetryable?: boolean;
    details?: Record<string, unknown>;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId ?? null;
    this.isRetryable = init.isRetryable ?? false;
    this.details = init.details ?? {};
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function call(path: string, init: RequestInit = {}): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/${path}`, init);
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new ApiError({
      code: "network_error",
      message: "The request could not be sent. Check the connection and try again.",
      status: 0,
      isRetryable: true,
    });
  }

  const headerId = response.headers.get("x-request-id");
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    body = undefined;
  }

  if (!response.ok) {
    if (isErrorEnvelope(body)) {
      const { code, message, details, request_id: requestId, is_retryable: isRetryable } = body.error;
      throw new ApiError({
        code,
        message,
        status: response.status,
        requestId: requestId || headerId,
        isRetryable: isRetryable === true,
        details: isRecord(details) ? details : {},
      });
    }
    throw new ApiError({
      code: "http_error",
      message: `The server answered with HTTP ${response.status}.`,
      status: response.status,
      requestId: headerId,
    });
  }
  if (body === undefined) {
    throw new ApiError({
      code: "invalid_response",
      message: "The server's answer was not JSON.",
      status: response.status,
      requestId: headerId,
    });
  }
  return body;
}

function postJson(path: string, payload: unknown, signal?: AbortSignal): Promise<unknown> {
  return call(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

function asQueryResponse(body: unknown): QueryResponse {
  if (!isQueryResponse(body)) {
    throw new ApiError({
      code: "invalid_response",
      message: "The answer is not in the documented response format.",
      status: 200,
    });
  }
  return body;
}

/** POST /v1/query: a question, with optional structured fields. */
export async function postQuery(request: QueryRequest, signal?: AbortSignal): Promise<QueryResponse> {
  return asQueryResponse(await postJson("v1/query", request, signal));
}

/** POST /v1/analyses: a typed plan, answered without a model. */
export async function postAnalysis(request: AnalysisRequest, signal?: AbortSignal): Promise<QueryResponse> {
  return asQueryResponse(await postJson("v1/analyses", request, signal));
}

// ---------------------------------------------------------------------------------------------
// Examples and capabilities. PLAN 5 does not fix the shape of these two endpoints, so they are
// read defensively: what cannot be read is left out, and the page falls back to its own list.
// ---------------------------------------------------------------------------------------------

export interface ExampleSummary {
  slug: string;
  title: string;
  request: QueryRequest;
}

export interface ExampleRun extends ExampleSummary {
  /** The recorded response, when the endpoint sends one. */
  response: QueryResponse | null;
}

export interface Capabilities {
  planner: { is_available: boolean; model: string | null } | null;
}

function firstText(...candidates: unknown[]): string | null {
  const found = candidates.find((candidate) => typeof candidate === "string" && candidate.trim() !== "");
  return typeof found === "string" ? found : null;
}

function toExample(item: unknown): ExampleRun | null {
  if (!isRecord(item)) {
    return null;
  }
  // The request comes from the backend; only its question is checked here.
  const request =
    isRecord(item.request) && typeof item.request.query === "string"
      ? (item.request as unknown as QueryRequest)
      : typeof item.query === "string"
        ? { query: item.query }
        : null;
  const slug = firstText(item.slug, item.id);
  if (request === null || slug === null) {
    return null;
  }
  return {
    slug,
    title: firstText(item.title, item.label, item.name) ?? request.query,
    request,
    response: isQueryResponse(item.response) ? item.response : null,
  };
}

export async function getExamples(signal?: AbortSignal): Promise<ExampleSummary[]> {
  const body = await call("v1/examples", { signal });
  const items = Array.isArray(body) ? body : isRecord(body) && Array.isArray(body.examples) ? body.examples : [];
  return items.flatMap((item) => {
    const example = toExample(item);
    return example ? [{ slug: example.slug, title: example.title, request: example.request }] : [];
  });
}

export async function getExample(slug: string, signal?: AbortSignal): Promise<ExampleRun> {
  const example = toExample(await call(`v1/examples/${encodeURIComponent(slug)}`, { signal }));
  if (example === null) {
    throw new ApiError({ code: "invalid_response", message: "The example could not be read.", status: 200 });
  }
  return example;
}

export async function getCapabilities(signal?: AbortSignal): Promise<Capabilities> {
  const body = await call("v1/capabilities", { signal });
  const planner = isRecord(body) && isRecord(body.planner) ? body.planner : null;
  return {
    planner:
      planner && typeof planner.is_available === "boolean"
        ? { is_available: planner.is_available, model: typeof planner.model === "string" ? planner.model : null }
        : null,
  };
}

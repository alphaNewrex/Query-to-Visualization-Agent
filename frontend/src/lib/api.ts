/**
 * Every call to the backend goes through the allow-listed proxy at /api/backend (PLAN 6.6), so the
 * browser never needs the backend's address and no CORS setup. A non-2xx answer carries the error
 * envelope of PLAN 5.7 and becomes an `ApiError`; so does a network failure or a body that is
 * not JSON, with a code of this file's own.
 */
import { ApiError } from "./api-error";
import { isErrorEnvelope, isQueryResponse, isRecord } from "./guards";
import { type StageEvent, readQueryStream } from "./stream";
import type { ChatRequest, Previous } from "./conversation";
import type { AnalysisRequest, QueryPlan, QueryRequest, QueryResponse } from "./types";

export { ApiError };

const BASE = "/api/backend";

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** The ApiError for a non-2xx answer: the backend's envelope when it sent one. */
function errorFrom(response: Response, body: unknown): ApiError {
  const headerId = response.headers.get("x-request-id");
  if (isErrorEnvelope(body)) {
    const { code, message, details, request_id: requestId, is_retryable: isRetryable } = body.error;
    return new ApiError({
      code,
      message,
      status: response.status,
      requestId: requestId || headerId,
      isRetryable: isRetryable === true,
      details: isRecord(details) ? details : {},
    });
  }
  return new ApiError({
    code: "http_error",
    message: `The server answered with HTTP ${response.status}.`,
    status: response.status,
    requestId: headerId,
  });
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
    throw errorFrom(response, body);
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
export async function postQuery(request: QueryRequest | ChatRequest, signal?: AbortSignal): Promise<QueryResponse> {
  return asQueryResponse(await postJson("v1/query", request, signal));
}

/** POST /v1/analyses: a typed plan, answered without a model. */
export async function postAnalysis(request: AnalysisRequest, signal?: AbortSignal): Promise<QueryResponse> {
  return asQueryResponse(await postJson("v1/analyses", request, signal));
}

// ---------------------------------------------------------------------------------------------
// The conversation: a question that may carry the answer before it, and the progress stream.
// ---------------------------------------------------------------------------------------------

/** The stream endpoint is not there (404 or 405), is not a stream, or could not be reached: the caller falls back to POST /v1/query. */
export class StreamUnavailableError extends Error {
  constructor() {
    super("The progress stream is not available.");
    this.name = "StreamUnavailableError";
  }
}

/** POST /v1/query/stream: stage events through `onStage`, then the answer. */
export async function postQueryStream(request: QueryRequest | ChatRequest, onStage: (stage: StageEvent) => void, signal?: AbortSignal): Promise<QueryResponse> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/v1/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new StreamUnavailableError();
  }
  if (response.status === 404 || response.status === 405) {
    void response.body?.cancel().catch(() => undefined);
    throw new StreamUnavailableError();
  }
  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch (error) {
      if (isAbortError(error)) {
        throw error;
      }
    }
    throw errorFrom(response, body);
  }
  if (!response.body || !(response.headers.get("content-type") ?? "").includes("text/event-stream")) {
    void response.body?.cancel().catch(() => undefined);
    throw new StreamUnavailableError();
  }
  return readQueryStream(response.body, onStage);
}

/** Whether the backend turned a request down because it does not know the `previous` field yet. */
function rejectsPrevious(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 400 || error.status === 422) &&
    JSON.stringify([error.message, error.details]).toLowerCase().includes("previous")
  );
}

export interface AskCallbacks {
  signal?: AbortSignal;
  onStage: (stage: StageEvent) => void;
  /** The stream is not there: the answer comes from POST /v1/query, with no steps to show. */
  onFallback: () => void;
}

/**
 * One conversational question: streamed when the backend can, plain otherwise, and without the
 * context when the backend does not know it. Returns the answer and what was finally sent.
 */
export async function askQuery(
  request: QueryRequest,
  previous: Previous | null,
  { signal, onStage, onFallback }: AskCallbacks,
): Promise<{ response: QueryResponse; sent: QueryRequest | ChatRequest }> {
  let streaming = true;
  const attempt = async (sent: QueryRequest | ChatRequest) => {
    if (streaming) {
      try {
        return { response: await postQueryStream(sent, onStage, signal), sent };
      } catch (error) {
        if (!(error instanceof StreamUnavailableError)) {
          throw error;
        }
        streaming = false;
        onFallback();
      }
    }
    return { response: await postQuery(sent, signal), sent };
  };
  if (!previous) {
    return attempt(request);
  }
  try {
    return await attempt({ ...request, previous });
  } catch (error) {
    if (!rejectsPrevious(error)) {
      throw error;
    }
    return attempt(request);
  }
}

// ---------------------------------------------------------------------------------------------
// Examples and capabilities (PLAN 4.17). The listing names each recorded run and says what it
// shows; one example carries its request, its plan and its response.
// ---------------------------------------------------------------------------------------------

/** One row of GET /v1/examples. */
export interface ExampleListing {
  slug: string;
  /** The recorded question. Two recordings can share it: one carries a structured field that the other lacks. */
  query: string;
  /** What the recording answered with: its chart type, or its `kind` when it has no chart. */
  outcome: string | null;
}

/** GET /v1/examples/{slug}: a recorded run in full. */
export interface RecordedExample {
  slug: string;
  request: QueryRequest;
  plan: QueryPlan;
  response: QueryResponse;
}

export interface Capabilities {
  planner: { is_available: boolean; model: string | null } | null;
}

export async function getExamples(signal?: AbortSignal): Promise<ExampleListing[]> {
  const body = await call("v1/examples", { signal });
  const items: unknown[] = Array.isArray(body) ? body : [];
  return items.flatMap((item) => {
    if (!isRecord(item) || typeof item.slug !== "string" || typeof item.query !== "string") {
      return [];
    }
    const type = typeof item.visualization_type === "string" ? item.visualization_type : null;
    const kind = typeof item.kind === "string" ? item.kind : null;
    return [{ slug: item.slug, query: item.query, outcome: type ?? kind }];
  });
}

export async function getExample(slug: string, signal?: AbortSignal): Promise<RecordedExample> {
  const body = await call(`v1/examples/${encodeURIComponent(slug)}`, { signal });
  // The backend wrote these files itself; only what the page relies on is checked.
  if (
    isRecord(body) &&
    typeof body.slug === "string" &&
    isRecord(body.request) &&
    typeof body.request.query === "string" &&
    isRecord(body.plan) &&
    isQueryResponse(body.response)
  ) {
    return {
      slug: body.slug,
      request: body.request as unknown as QueryRequest,
      plan: body.plan as unknown as QueryPlan,
      response: body.response,
    };
  }
  throw new ApiError({ code: "invalid_response", message: "The example could not be read.", status: 200 });
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

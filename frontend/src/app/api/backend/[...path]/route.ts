/**
 * Allow-listed proxy from the browser to the backend API.
 *
 * The browser only ever calls this origin, so the backend needs no CORS configuration. A Route
 * Handler does this job instead of `rewrites()` because a rewrite destination is fixed at build
 * time, whereas BACKEND_URL is read here on every request.
 */

type AllowedMethod = "GET" | "POST";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

/** Just above the backend's own 45 second deadline, so that its 504 arrives before this fires. */
const BACKEND_TIMEOUT_MS = 50_000;

/** The method each reachable path accepts. A Map, so that no inherited object key passes for a path. */
const ROUTE_METHODS = new Map<string, AllowedMethod>([
  ["v1/query", "POST"],
  ["v1/analyses", "POST"],
  ["v1/examples", "GET"],
  ["v1/capabilities", "GET"],
]);

/** One example by slug. A slug holds no dot and no slash, so it cannot climb out of `v1/examples`. */
const EXAMPLE_ROUTE = /^v1\/examples\/[A-Za-z0-9_-]+$/;

/** The errors this proxy answers itself, in the backend's error envelope. */
const PROXY_ERRORS = {
  not_found: { status: 404, isRetryable: false },
  method_not_allowed: { status: 405, isRetryable: false },
  backend_unreachable: { status: 502, isRetryable: true },
} as const;

function allowedMethod(path: string): AllowedMethod | undefined {
  return ROUTE_METHODS.get(path) ?? (EXAMPLE_ROUTE.test(path) ? "GET" : undefined);
}

function errorResponse(
  code: keyof typeof PROXY_ERRORS,
  message: string,
  requestId: string,
  headers?: Record<string, string>,
): Response {
  const { status, isRetryable } = PROXY_ERRORS[code];
  return Response.json(
    { error: { code, message, details: {}, request_id: requestId, is_retryable: isRetryable } },
    { status, headers: { "Cache-Control": "no-store", "X-Request-ID": requestId, ...headers } },
  );
}

function isJson(text: string): boolean {
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
}

/** Only the content type and the request id travel on: cookies and other browser headers stop here. */
function forwardedHeaders(request: Request, requestId: string): Headers {
  const headers = new Headers({ "X-Request-ID": requestId });
  const contentType = request.headers.get("content-type");
  if (contentType !== null) {
    headers.set("Content-Type", contentType);
  }
  return headers;
}

function unreachableMessage(error: unknown): string {
  return error instanceof Error && error.name === "TimeoutError"
    ? `The backend did not answer within ${BACKEND_TIMEOUT_MS / 1000} seconds.`
    : "The backend could not be reached.";
}

/** Sends an allow-listed request to the backend and returns its status and body as they are. */
async function forward(request: Request, path: string, requestId: string): Promise<Response> {
  try {
    const upstream = await fetch(new URL(`/${path}`, process.env.BACKEND_URL ?? DEFAULT_BACKEND_URL), {
      method: request.method,
      headers: forwardedHeaders(request, requestId),
      body: request.method === "POST" ? await request.arrayBuffer() : undefined,
      signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
      // Next.js gives `fetch` a data cache of its own; a proxied answer must never come from it.
      cache: "no-store",
    });
    // Read in full inside the `try`, so that a connection lost midway is reported like any other.
    const body = await upstream.text();
    if (!isJson(body)) {
      return errorResponse("backend_unreachable", "The backend answered with a body that is not JSON.", requestId);
    }
    // The text goes back as received, not re-serialised. No backend header is copied except the
    // request id: `fetch` has already decoded the body, so its encoding and length no longer apply.
    return new Response(body, {
      status: upstream.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Request-ID": upstream.headers.get("x-request-id") ?? requestId,
      },
    });
  } catch (error) {
    return errorResponse("backend_unreachable", unreachableMessage(error), requestId);
  }
}

async function handle(request: Request, context: RouteContext<"/api/backend/[...path]">): Promise<Response> {
  // One id follows the request end to end. The backend adopts an incoming X-Request-ID, so the id
  // in an error raised here can be found in the backend's log as well.
  const requestId = request.headers.get("x-request-id") || crypto.randomUUID();
  // The query string is left behind on purpose: no allow-listed route takes one.
  const path = (await context.params).path.join("/");

  const method = allowedMethod(path);
  if (method === undefined) {
    return errorResponse("not_found", "This path is not on the proxy's allow-list.", requestId);
  }
  if (request.method !== method) {
    return errorResponse("method_not_allowed", `This path accepts ${method} only.`, requestId, { Allow: method });
  }
  return forward(request, path, requestId);
}

// Every method Next.js can route is exported, so that a refused method is answered with the error
// envelope and not with the framework's own empty 405.
export {
  handle as GET,
  handle as POST,
  handle as PUT,
  handle as PATCH,
  handle as DELETE,
  handle as HEAD,
  handle as OPTIONS,
};

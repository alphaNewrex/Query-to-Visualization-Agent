// @vitest-environment node
// The Route Handler runs on the server, so it is tested with Node's own Request, Response and
// AbortSignal and not with the browser stand-ins of the default jsdom environment.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as route from "@/app/api/backend/[...path]/route";

type Method = keyof typeof route;

const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

/** Stands in for the backend: every call the proxy makes with `fetch` lands here. */
const backend = vi.fn<typeof fetch>();

function backendAnswers(body: BodyInit, init?: ResponseInit): void {
  backend.mockImplementation(async () => new Response(body, init));
}

/**
 * Calls the Route Handler the way Next.js does for `METHOD /api/backend/{target}`: the path
 * segments arrive as `params`, and a query string stays on the request URL only.
 */
function request(method: Method, target: string, init?: RequestInit): Promise<Response> {
  const incoming = new Request(`http://localhost:3000/api/backend/${target}`, { ...init, method });
  const path = target.split("?")[0].split("/");
  return route[method](incoming, { params: Promise.resolve({ path }) });
}

/** The request the proxy sent to the backend, rebuilt so that its body can be read. */
function forwarded(): Request {
  const [input, init] = backend.mock.calls[0];
  return new Request(input, init);
}

/** Checks the whole envelope. Any message passes unless `expected.message` narrows it. */
async function expectErrorEnvelope(
  response: Response,
  expected: { status: number; code: string; isRetryable: boolean; message?: RegExp },
): Promise<void> {
  const requestId = response.headers.get("x-request-id");

  expect(response.status).toBe(expected.status);
  expect(response.headers.get("content-type")).toBe("application/json");
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(requestId).toMatch(/\S/);
  expect(await response.json()).toEqual({
    error: {
      code: expected.code,
      message: expect.stringMatching(expected.message ?? /\S/),
      details: {},
      request_id: requestId,
      is_retryable: expected.isRetryable,
    },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", backend);
  vi.stubEnv("BACKEND_URL", "http://backend.test:9000");
  backendAnswers("{}");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  backend.mockReset();
});

describe("allow-listed routes", () => {
  it.each([
    ["POST", "v1/query", "http://backend.test:9000/v1/query"],
    ["POST", "v1/analyses", "http://backend.test:9000/v1/analyses"],
    ["GET", "v1/examples", "http://backend.test:9000/v1/examples"],
    ["GET", "v1/examples/01-pembrolizumab-by-year", "http://backend.test:9000/v1/examples/01-pembrolizumab-by-year"],
    ["GET", "v1/capabilities", "http://backend.test:9000/v1/capabilities"],
  ] as const)("forwards %s %s to BACKEND_URL", async (method, path, backendUrl) => {
    await request(method, path);

    expect(backend).toHaveBeenCalledTimes(1);
    expect(forwarded().method).toBe(method);
    expect(forwarded().url).toBe(backendUrl);
  });

  it("forwards to http://127.0.0.1:8000 when BACKEND_URL is not set", async () => {
    vi.stubEnv("BACKEND_URL", undefined);

    await request("GET", "v1/capabilities");

    expect(forwarded().url).toBe("http://127.0.0.1:8000/v1/capabilities");
  });

  it("reads BACKEND_URL on every request, not once when the module loads", async () => {
    await request("GET", "v1/capabilities");
    vi.stubEnv("BACKEND_URL", "http://other.test:9001");
    await request("GET", "v1/capabilities");

    expect(backend.mock.calls.map(([url]) => String(url))).toEqual([
      "http://backend.test:9000/v1/capabilities",
      "http://other.test:9001/v1/capabilities",
    ]);
  });

  it("forwards an example slug made of letters, digits, hyphens and underscores", async () => {
    await request("GET", "v1/examples/Phase_3-trials");

    expect(forwarded().url).toBe("http://backend.test:9000/v1/examples/Phase_3-trials");
  });

  it("leaves the query string behind, as no allow-listed route takes one", async () => {
    await request("GET", "v1/capabilities?next=/healthz");

    expect(forwarded().url).toBe("http://backend.test:9000/v1/capabilities");
  });

  it("forwards the request body and its content type unchanged", async () => {
    const body = '{ "query": "Pembrolizumab trials by year",\n  "drug_name": "Pembrolizumab" }';

    await request("POST", "v1/query", { body, headers: { "Content-Type": "application/json" } });

    expect(forwarded().headers.get("content-type")).toBe("application/json");
    await expect(forwarded().text()).resolves.toBe(body);
  });

  it("forwards a request body that is not valid UTF-8 byte for byte", async () => {
    const body = new Uint8Array([0x7b, 0x22, 0xff, 0x22, 0x7d]);

    await request("POST", "v1/query", { body });

    expect(new Uint8Array(await forwarded().arrayBuffer())).toEqual(body);
  });

  it("keeps the browser's other headers away from the backend", async () => {
    await request("GET", "v1/capabilities", { headers: { Cookie: "session=1", Authorization: "Bearer token" } });

    expect(forwarded().headers.get("cookie")).toBeNull();
    expect(forwarded().headers.get("authorization")).toBeNull();
  });

  it("asks for an answer that bypasses the Next.js data cache", async () => {
    await request("GET", "v1/capabilities");

    expect(forwarded().cache).toBe("no-store");
  });

  it("gives the backend 50 seconds to answer", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");

    await request("GET", "v1/capabilities");

    expect(timeout).toHaveBeenCalledExactlyOnceWith(50_000);
    expect(backend.mock.calls[0][1]?.signal).toBe(timeout.mock.results[0].value);
  });
});

describe("backend responses", () => {
  it("passes the body through unchanged", async () => {
    const body = '{ "kind": "visualization",\n  "value": 1.0, "label": "caf\\u00e9" }';
    backendAnswers(body);

    const response = await request("POST", "v1/query");

    await expect(response.text()).resolves.toBe(body);
  });

  it.each([200, 422, 503])("passes status %i through", async (status) => {
    backendAnswers("{}", { status });

    const response = await request("POST", "v1/query");

    expect(response.status).toBe(status);
  });

  it("passes the backend's X-Request-ID through", async () => {
    backendAnswers("{}", { headers: { "X-Request-ID": "id-from-backend" } });

    const response = await request("GET", "v1/capabilities");

    expect(response.headers.get("x-request-id")).toBe("id-from-backend");
  });

  it("answers as uncacheable JSON, without the encoding header of a body fetch has already decoded", async () => {
    backendAnswers("{}", { headers: { "Content-Type": "application/json", "Content-Encoding": "gzip" } });

    const response = await request("GET", "v1/capabilities");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("content-encoding")).toBeNull();
  });
});

describe("refused requests", () => {
  it.each([
    ["healthz", "a backend route that is not exposed"],
    ["v1/schema/contract", "a backend route that is not exposed"],
    ["v1/query/extra", "a longer path under an allowed one"],
    ["v1/examples/01-pembrolizumab-by-year/response", "a longer path under an example"],
    ["api/v1/examples/01-pembrolizumab-by-year", "an example under another prefix"],
    ["v1/examples/", "an example with an empty slug"],
    ["v1/examples/..", "a slug that climbs out of the examples"],
    ["v1/examples/../../docs", "a slug that climbs out of the examples"],
    ["constructor", "a property every JavaScript object has"],
  ])("answers 404 not_found for %s (%s)", async (path) => {
    const response = await request("GET", path);

    await expectErrorEnvelope(response, { status: 404, code: "not_found", isRetryable: false });
    expect(backend).not.toHaveBeenCalled();
  });

  it.each([
    ["GET", "v1/query", "POST"],
    ["PUT", "v1/query", "POST"],
    ["DELETE", "v1/analyses", "POST"],
    ["POST", "v1/examples", "GET"],
    ["PATCH", "v1/examples/01-pembrolizumab-by-year", "GET"],
    ["HEAD", "v1/capabilities", "GET"],
    ["OPTIONS", "v1/capabilities", "GET"],
  ] as const)("answers 405 method_not_allowed for %s %s", async (method, path, allowed) => {
    const response = await request(method, path);

    await expectErrorEnvelope(response, { status: 405, code: "method_not_allowed", isRetryable: false });
    expect(response.headers.get("allow")).toBe(allowed);
    expect(backend).not.toHaveBeenCalled();
  });
});

describe("backend failures", () => {
  const unreachable = { status: 502, code: "backend_unreachable", isRetryable: true };

  it("answers 502 backend_unreachable when the connection is refused", async () => {
    backend.mockRejectedValue(new TypeError("fetch failed", { cause: new Error("connect ECONNREFUSED") }));

    const response = await request("GET", "v1/capabilities");

    await expectErrorEnvelope(response, { ...unreachable, message: /could not be reached/ });
  });

  it("answers 502 backend_unreachable, saying how long it waited, when the backend times out", async () => {
    backend.mockRejectedValue(new DOMException("The operation was aborted due to timeout", "TimeoutError"));

    const response = await request("POST", "v1/query");

    await expectErrorEnvelope(response, { ...unreachable, message: /within 50 seconds/ });
  });

  it("keeps the backend's address and the cause of the failure out of its answer", async () => {
    const cause = new Error("connect ECONNREFUSED 10.1.2.3:9000");
    vi.stubEnv("BACKEND_URL", "http://user:secret@backend.test:9000");
    backend.mockRejectedValue(new TypeError("fetch failed", { cause }));

    const answer = await (await request("GET", "v1/capabilities")).text();

    expect(answer).not.toMatch(/backend\.test|secret|fetch failed|ECONNREFUSED|10\.1\.2\.3/);
  });

  it("answers 502 backend_unreachable when the connection drops while the body is read", async () => {
    const droppedBody = new ReadableStream({ start: (controller) => controller.error(new TypeError("terminated")) });
    backend.mockResolvedValue(new Response(droppedBody));

    await expectErrorEnvelope(await request("POST", "v1/query"), unreachable);
  });

  it.each([
    ["an HTML error page", "<html><body>Bad Gateway</body></html>"],
    ["an empty body", ""],
    ["JSON that is cut off", '{"kind": "visuali'],
  ])("answers 502 backend_unreachable when the backend sends %s", async (_, body) => {
    backendAnswers(body, { status: 200 });

    await expectErrorEnvelope(await request("POST", "v1/query"), unreachable);
  });

  it("answers 502 backend_unreachable when BACKEND_URL is not a URL", async () => {
    vi.stubEnv("BACKEND_URL", "not a url");

    await expectErrorEnvelope(await request("GET", "v1/capabilities"), unreachable);
  });
});

describe("request ids", () => {
  it("sends the caller's X-Request-ID on to the backend", async () => {
    await request("GET", "v1/capabilities", { headers: { "X-Request-ID": "id-from-caller" } });

    expect(forwarded().headers.get("x-request-id")).toBe("id-from-caller");
  });

  it("makes an id of its own when the caller's X-Request-ID is empty", async () => {
    await request("GET", "v1/capabilities", { headers: { "X-Request-ID": "" } });

    expect(forwarded().headers.get("x-request-id")).toMatch(UUID);
  });

  it("answers with the id it sent to the backend when the backend returns none", async () => {
    const response = await request("GET", "v1/capabilities");

    const sentToBackend = forwarded().headers.get("x-request-id");
    expect(sentToBackend).toMatch(UUID);
    expect(response.headers.get("x-request-id")).toBe(sentToBackend);
  });

  it("reports the id it sent to the backend when the backend cannot be reached", async () => {
    backend.mockRejectedValue(new TypeError("fetch failed"));

    const response = await request("GET", "v1/capabilities");

    const sentToBackend = forwarded().headers.get("x-request-id");
    expect(sentToBackend).toMatch(UUID);
    expect((await response.json()).error.request_id).toBe(sentToBackend);
  });
});

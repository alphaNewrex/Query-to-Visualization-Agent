import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api-error";
import { readQueryStream, SseParser, type StageEvent } from "@/lib/stream";

import { timeSeries } from "./fixtures/responses";

const stage = (step: string, status: "started" | "done", summary = "", detail: object = {}) =>
  `event: stage\ndata: ${JSON.stringify({ step, status, summary, detail })}\n\n`;
const result = (body: unknown = timeSeries) => `event: result\ndata: ${JSON.stringify(body)}\n\n`;
const failure = (code = "upstream_error", retryable = true) =>
  `event: error\ndata: ${JSON.stringify({ error: { code, message: "It broke.", details: { step: "execute" }, request_id: "req-9", is_retryable: retryable } })}\n\n`;

/** A body that delivers each string as one chunk, as the network might. */
function body(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      controller.close();
    },
  });
}

async function read(...chunks: (string | Uint8Array)[]) {
  const stages: StageEvent[] = [];
  const response = await readQueryStream(body(...chunks), (item) => stages.push(item));
  return { stages, response };
}

describe("SseParser", () => {
  it("returns an event only once its blank line has arrived", () => {
    const parser = new SseParser();
    expect(parser.push("event: stage\ndata: {}\n")).toEqual([]);
    expect(parser.push("\n")).toEqual([{ event: "stage", data: "{}" }]);
  });

  it("handles CRLF line ends, comments, multi-line data and a missing space after the colon", () => {
    const parser = new SseParser();
    const out = parser.push(": keep-alive\r\nevent:result\r\ndata: a\r\ndata: b\r\n\r\n");
    expect(out).toEqual([{ event: "result", data: "a\nb" }]);
  });

  it("does not take the first half of a CRLF for a line end", () => {
    const parser = new SseParser();
    expect(parser.push("event: x\r\ndata: 1\r")).toEqual([]);
    expect(parser.push("\n\r\n")).toEqual([{ event: "x", data: "1" }]);
  });

  it("names an event without a name a message", () => {
    expect(new SseParser().push("data: hi\n\n")).toEqual([{ event: "message", data: "hi" }]);
  });
});

describe("readQueryStream", () => {
  it("passes the stage events on in order and returns the result", async () => {
    const { stages, response } = await read(
      stage("plan", "started", "Understanding the question"),
      stage("plan", "done", "Understanding the question"),
      stage("resolve", "done", "Looking up the drug: 2,971 trials", { trials: 2971 }),
      result(),
    );
    expect(stages.map((item) => `${item.step}:${item.status}`)).toEqual(["plan:started", "plan:done", "resolve:done"]);
    expect(stages[2]).toEqual({ step: "resolve", status: "done", summary: "Looking up the drug: 2,971 trials", detail: { trials: 2971 } });
    expect(response).toEqual(timeSeries);
  });

  it("copes with a chunk that ends in the middle of an event", async () => {
    const text = stage("execute", "started", "Fetching 12 of 18") + result();
    for (const cut of [5, 20, 47, text.indexOf("event: result") + 8, text.length - 3]) {
      const { stages, response } = await read(text.slice(0, cut), text.slice(cut));
      expect(stages).toHaveLength(1);
      expect(response).toEqual(timeSeries);
    }
  });

  it("copes with one chunk per byte, and with a character split across chunks", async () => {
    const bytes = new TextEncoder().encode(stage("plan", "done", "Prüfen der Frage — fertig") + result());
    const { stages, response } = await read(...Array.from(bytes, (byte) => new Uint8Array([byte])));
    expect(stages[0].summary).toBe("Prüfen der Frage — fertig");
    expect(response).toEqual(timeSeries);
  });

  it("throws the error event as an ApiError with the backend's code, request id and retryability", async () => {
    const error = await read(stage("plan", "started"), failure("planner_unavailable", false)).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "planner_unavailable", message: "It broke.", requestId: "req-9", isRetryable: false, details: { step: "execute" } });
  });

  it("still reports the stages that came before an error", async () => {
    const stages: StageEvent[] = [];
    await expect(readQueryStream(body(stage("plan", "done"), failure()), (item) => stages.push(item))).rejects.toBeInstanceOf(ApiError);
    expect(stages).toHaveLength(1);
  });

  it("ignores events it does not know and stage events it cannot read", async () => {
    const { stages, response } = await read(
      "event: ping\ndata: 1\n\n",
      "event: stage\ndata: not json\n\n",
      'event: stage\ndata: {"step":"plan","status":"sideways"}\n\n',
      stage("build", "started"),
      result(),
    );
    expect(stages.map((item) => item.step)).toEqual(["build"]);
    expect(response).toEqual(timeSeries);
  });

  it("calls a stream that ends without a result an interrupted connection", async () => {
    const error = await read(stage("plan", "started"), 'event: result\ndata: {"kind":').catch((e: unknown) => e);
    expect(error).toMatchObject({ code: "stream_interrupted", isRetryable: true });
  });

  it("calls a result that is not a response an invalid response", async () => {
    const error = await read(result({ hello: "world" })).catch((e: unknown) => e);
    expect(error).toMatchObject({ code: "invalid_response" });
  });

  it("calls an error event without the envelope a stream error", async () => {
    const error = await read('event: error\ndata: "boom"\n\n').catch((e: unknown) => e);
    expect(error).toMatchObject({ code: "stream_error" });
  });
});

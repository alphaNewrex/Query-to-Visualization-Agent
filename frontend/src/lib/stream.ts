/**
 * The progress stream of POST /v1/query/stream: Server-Sent Events, `stage` events while the agent
 * works and then one `result` or one `error`. The parser is split from the network so that it can
 * be fed any chunking, including a chunk that ends in the middle of an event or of a character.
 */
import { ApiError } from "./api-error";
import { isErrorEnvelope, isQueryResponse, isRecord } from "./guards";
import type { QueryResponse } from "./types";

export interface SseMessage {
  event: string;
  data: string;
}

/** Incremental parser of the text/event-stream format. An event is complete only at its blank line. */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];

  /** Feeds a chunk of text and returns the events it completed. */
  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    for (;;) {
      const match = /\r\n|\n|\r/.exec(this.buffer);
      if (!match) {
        break;
      }
      // A lone "\r" at the very end may be the first half of "\r\n": wait for the next chunk.
      if (match[0] === "\r" && match.index === this.buffer.length - 1) {
        break;
      }
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const message = this.line(line);
      if (message) {
        out.push(message);
      }
    }
    return out;
  }

  private line(line: string): SseMessage | null {
    if (line === "") {
      const message = this.data.length > 0 ? { event: this.event || "message", data: this.data.join("\n") } : null;
      this.event = "";
      this.data = [];
      return message;
    }
    if (line.startsWith(":")) {
      return null;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      this.event = value;
    } else if (field === "data") {
      this.data.push(value);
    }
    return null;
  }
}

/** One step of the agent, as the stream reports it. */
export interface StageEvent {
  /** "plan", "check", "resolve", "strategy", "execute" or "build"; any other name is kept. */
  step: string;
  status: "started" | "done";
  summary: string;
  detail: Record<string, unknown>;
}

export type StreamEvent =
  | { type: "stage"; stage: StageEvent }
  | { type: "result"; response: QueryResponse }
  | { type: "error"; error: ApiError };

/** Reads one message. A `stage` that cannot be read and an event of an unknown name are skipped (null). */
export function readEvent(message: SseMessage): StreamEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(message.data);
  } catch {
    value = undefined;
  }
  switch (message.event) {
    case "stage": {
      if (!isRecord(value) || typeof value.step !== "string" || (value.status !== "started" && value.status !== "done")) {
        return null;
      }
      return {
        type: "stage",
        stage: {
          step: value.step,
          status: value.status,
          summary: typeof value.summary === "string" ? value.summary : "",
          detail: isRecord(value.detail) ? value.detail : {},
        },
      };
    }
    case "result":
      if (!isQueryResponse(value)) {
        return {
          type: "error",
          error: new ApiError({ code: "invalid_response", message: "The answer is not in the documented response format.", status: 200 }),
        };
      }
      return { type: "result", response: value };
    case "error": {
      if (!isErrorEnvelope(value)) {
        return { type: "error", error: new ApiError({ code: "stream_error", message: "The agent stopped with an error.", status: 0 }) };
      }
      const { code, message: text, details, request_id: requestId, is_retryable: isRetryable } = value.error;
      return {
        type: "error",
        error: new ApiError({
          code,
          message: text,
          status: 0,
          requestId: requestId || null,
          isRetryable: isRetryable === true,
          details: isRecord(details) ? details : {},
        }),
      };
    }
    default:
      return null;
  }
}

/**
 * Reads the stream to its `result`, calling `onStage` for each step on the way. An `error` event
 * is thrown as an ApiError; a stream that ends with neither is an interrupted connection.
 */
export async function readQueryStream(body: ReadableStream<Uint8Array>, onStage: (stage: StageEvent) => void): Promise<QueryResponse> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const handle = (messages: SseMessage[]): QueryResponse | null => {
    for (const message of messages) {
      const event = readEvent(message);
      if (event?.type === "stage") {
        onStage(event.stage);
      } else if (event?.type === "error") {
        throw event.error;
      } else if (event?.type === "result") {
        return event.response;
      }
    }
    return null;
  };
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          throw error;
        }
        break;
      }
      if (chunk.done) {
        break;
      }
      const response = handle(parser.push(decoder.decode(chunk.value, { stream: true })));
      if (response) {
        return response;
      }
    }
  } finally {
    void reader.cancel().catch(() => undefined);
  }
  throw new ApiError({
    code: "stream_interrupted",
    message: "The connection closed before the answer arrived.",
    status: 0,
    isRetryable: true,
  });
}

import type { ErrorResponse, MessageResponse, QueryResponse, Viz, VizType } from "./types";

/** The contract's major version this client renders (PLAN 5.9). Minor versions only add keys. */
export const SUPPORTED_SPEC_MAJOR = 1;

/** Every visualization type the client has a renderer for. A type missing here is a compile error. */
const KNOWN_TYPES: Record<VizType, true> = {
  bar_chart: true,
  time_series: true,
  histogram: true,
  scatter_plot: true,
  network_graph: true,
  table: true,
  metric: true,
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The error body of PLAN 5.7: `{"error": {"code", "message", ...}}`. */
export function isErrorEnvelope(value: unknown): value is ErrorResponse {
  return (
    isRecord(value) &&
    isRecord(value.error) &&
    typeof value.error.code === "string" &&
    typeof value.error.message === "string"
  );
}

/**
 * The seven keys every HTTP 200 body carries. Deliberately shallow: the backend guarantees the
 * rest, and an unexpected inner shape is caught later by the error boundary.
 */
export function isQueryResponse(value: unknown): value is QueryResponse {
  return (
    isRecord(value) &&
    typeof value.spec_version === "string" &&
    typeof value.kind === "string" &&
    typeof value.message === "string" &&
    isRecord(value.references) &&
    isRecord(value.meta)
  );
}

export function specMajor(specVersion: string): number {
  return Number.parseInt(specVersion.split(".")[0] ?? "", 10);
}

export function isSupportedVersion(response: { spec_version: string }): boolean {
  return specMajor(response.spec_version) === SUPPORTED_SPEC_MAJOR;
}

/** A visualization whose `type` has a renderer. */
export function isKnownVisualization(value: unknown): value is Viz {
  return isRecord(value) && typeof value.type === "string" && Object.hasOwn(KNOWN_TYPES, value.type);
}

/**
 * How a response is shown. Anything the client does not know goes to "fallback" (message, rows
 * as a table, raw JSON), as PLAN 5.9 asks: an unknown `kind` shows `message`, and an unknown
 * `type` or major version falls back to a table of the rows.
 */
export type Outcome =
  | { tag: "chart"; response: Extract<QueryResponse, { kind: "visualization" }>; visualization: Viz }
  | { tag: "clarification"; response: Extract<QueryResponse, { kind: "clarification" }> }
  | { tag: "message"; response: MessageResponse }
  | { tag: "conversation"; response: MessageResponse }
  | { tag: "fallback"; response: QueryResponse };

export function classify(response: QueryResponse): Outcome {
  if (!isSupportedVersion(response)) {
    return { tag: "fallback", response };
  }
  switch (response.kind) {
    case "visualization":
      return isKnownVisualization(response.visualization)
        ? { tag: "chart", response, visualization: response.visualization }
        : { tag: "fallback", response };
    case "clarification":
      return isRecord(response.clarification) ? { tag: "clarification", response } : { tag: "fallback", response };
    case "no_data":
    case "unsupported":
      return { tag: "message", response };
    case "conversation":
      return { tag: "conversation", response };
    default:
      return { tag: "fallback", response };
  }
}

/** Links built from registry data are followed only when they are web addresses. */
export function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  try {
    const { protocol } = new URL(value);
    return protocol === "https:" || protocol === "http:";
  } catch {
    return false;
  }
}

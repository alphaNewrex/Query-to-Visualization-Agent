/**
 * The contract types the app uses, derived from the seven names the generated file guarantees
 * (QueryRequest, AnalysisRequest, QueryPlan, QueryResponse, ErrorResponse, Datum, Citation).
 * Everything else is read off `QueryResponse`, so a rename of an inner type in the generated
 * schema does not reach the components.
 */
import type {
  AnalysisRequest,
  Citation,
  Datum,
  ErrorResponse,
  QueryPlan,
  QueryRequest,
  QueryResponse,
} from "./contract.gen";

export type { AnalysisRequest, Citation, Datum, ErrorResponse, QueryPlan, QueryRequest, QueryResponse };

export type VisualizationResponse = Extract<QueryResponse, { kind: "visualization" }>;
export type ClarificationResponse = Extract<QueryResponse, { kind: "clarification" }>;
export type MessageResponse = Extract<QueryResponse, { kind: "no_data" | "unsupported" | "conversation" }>;

export type Viz = NonNullable<QueryResponse["visualization"]>;
export type VizType = Viz["type"];
export type VizOf<K extends VizType> = Extract<Viz, { type: K }>;

export type Meta = QueryResponse["meta"];
export type Reference = QueryResponse["references"][string];
export type ScopeEvidence = Reference["scope_evidence"][number];
export type Clarification = NonNullable<QueryResponse["clarification"]>;
export type RequestOptions = NonNullable<Meta["options"]>;

export type CategoryChannel = VizOf<"bar_chart">["encoding"]["x"];
export type TemporalChannel = VizOf<"time_series">["encoding"]["x"];
export type QuantitativeChannel = VizOf<"bar_chart">["encoding"]["y"];
export type FieldDef = VizOf<"table">["encoding"]["columns"][number];
export type NetworkData = VizOf<"network_graph">["data"];
export type NetworkNode = NetworkData["nodes"][number];
export type NetworkEdge = NetworkData["edges"][number];

/** What a renderer reports when a mark is selected (plan 6.3). */
export type DatumSelection = { label: string; value: string; datum: Datum };

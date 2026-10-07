/**
 * The query form's values and their translation to and from a `QueryRequest` (PLAN 4.3). Only the
 * fields the form offers are covered; a ready-made request from a follow-up or a clarification
 * option is posted as it is and only mirrored into the form.
 */
import type { QueryRequest } from "./types";

type Phase = NonNullable<QueryRequest["trial_phase"]>[number];
type Status = NonNullable<QueryRequest["status"]>[number];

export const PHASES = [
  { value: "EARLY_PHASE1", label: "Early phase 1" },
  { value: "PHASE1", label: "Phase 1" },
  { value: "PHASE2", label: "Phase 2" },
  { value: "PHASE3", label: "Phase 3" },
  { value: "PHASE4", label: "Phase 4" },
  { value: "NA", label: "Not applicable" },
] as const satisfies readonly { value: Phase; label: string }[];

export const STATUSES = [
  "RECRUITING",
  "NOT_YET_RECRUITING",
  "ENROLLING_BY_INVITATION",
  "ACTIVE_NOT_RECRUITING",
  "COMPLETED",
  "SUSPENDED",
  "TERMINATED",
  "WITHDRAWN",
  "UNKNOWN",
  "AVAILABLE",
  "NO_LONGER_AVAILABLE",
  "TEMPORARILY_NOT_AVAILABLE",
  "APPROVED_FOR_MARKETING",
  "WITHHELD",
] as const satisfies readonly Status[];

/** "ACTIVE_NOT_RECRUITING" to "Active not recruiting". */
export function statusLabel(token: string): string {
  const words = token.toLowerCase().split("_").join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const TEXT_FIELDS = [
  { name: "drug_name", label: "Drug", placeholder: "pembrolizumab" },
  { name: "condition", label: "Condition", placeholder: "lung cancer" },
  { name: "sponsor", label: "Sponsor", placeholder: "Merck Sharp & Dohme" },
  { name: "country", label: "Country", placeholder: "United States" },
] as const;

type TextField = (typeof TEXT_FIELDS)[number]["name"];

export interface FormValues extends Record<TextField, string> {
  query: string;
  trial_phase: Phase[];
  status: Status | "";
  start_year: string;
  end_year: string;
}

export const EMPTY_FORM: FormValues = {
  query: "",
  drug_name: "",
  condition: "",
  sponsor: "",
  country: "",
  trial_phase: [],
  status: "",
  start_year: "",
  end_year: "",
};

/** The request fields the collapsible block holds; a clarification that names one opens the block. */
export const STRUCTURED_FIELDS: ReadonlySet<string> = new Set([
  ...TEXT_FIELDS.map((field) => field.name),
  "trial_phase",
  "status",
  "start_year",
  "end_year",
]);

/** The id of the input for a request field, so that a clarification can focus it. */
export function inputId(field: string): string {
  return `field-${field}`;
}

export interface FormErrors {
  query?: string;
  start_year?: string;
  end_year?: string;
}

function parseYear(text: string): number | null {
  return /^\d{4}$/.test(text.trim()) ? Number(text.trim()) : null;
}

export function validate(values: FormValues): FormErrors {
  const errors: FormErrors = {};
  const query = values.query.trim();
  if (query.length < 3) {
    errors.query = "Write at least three characters.";
  } else if (query.length > 1000) {
    errors.query = "Keep the question under 1,000 characters.";
  } else if (!/\p{L}/u.test(query)) {
    errors.query = "The question needs at least one letter.";
  }
  for (const field of ["start_year", "end_year"] as const) {
    const text = values[field].trim();
    const year = parseYear(text);
    if (text !== "" && (year === null || year < 1900 || year > 2100)) {
      errors[field] = "Use a year from 1900 to 2100.";
    }
  }
  const from = parseYear(values.start_year);
  const to = parseYear(values.end_year);
  if (errors.end_year === undefined && from !== null && to !== null && from > to) {
    errors.end_year = "The end year is before the start year.";
  }
  return errors;
}

export function toRequest(values: FormValues): QueryRequest {
  const request: QueryRequest = { query: values.query.trim() };
  for (const { name } of TEXT_FIELDS) {
    const text = values[name].trim();
    if (text !== "") {
      request[name] = [text];
    }
  }
  if (values.trial_phase.length > 0) {
    request.trial_phase = values.trial_phase;
  }
  if (values.status !== "") {
    request.status = [values.status];
  }
  const from = parseYear(values.start_year);
  const to = parseYear(values.end_year);
  if (from !== null) {
    request.start_year = from;
  }
  if (to !== null) {
    request.end_year = to;
  }
  return request;
}

function joined(value: string | string[] | null | undefined): string {
  return Array.isArray(value) ? value.join(", ") : (value ?? "");
}

/** Mirrors a request into the form, so that the page shows what was asked. */
export function fromRequest(request: QueryRequest): FormValues {
  return {
    query: request.query,
    drug_name: joined(request.drug_name),
    condition: joined(request.condition),
    sponsor: joined(request.sponsor),
    country: joined(request.country),
    trial_phase: request.trial_phase ?? [],
    status: request.status?.[0] ?? "",
    start_year: request.start_year == null ? "" : String(request.start_year),
    end_year: request.end_year == null ? "" : String(request.end_year),
  };
}

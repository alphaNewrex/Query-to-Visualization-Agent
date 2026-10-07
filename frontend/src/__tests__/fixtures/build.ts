// Small builders for the hand-written fixtures. Everything is typed against the generated
// contract, so a fixture that breaks the contract does not compile.
import type {
  AppliedFilters,
  CategoryChannel,
  Citation,
  Datum,
  FieldDef,
  Meta,
  QuantitativeChannel,
  QueryRequest,
  TrialReference,
} from "@/lib/contract.gen";

export const DATA_DATE = "2026-10-06";

export const emptyFilters: AppliedFilters = {
  drug_name: [],
  condition: [],
  sponsor: [],
  country: [],
  term: [],
  trial_phase: [],
  status: [],
  study_type: [],
  sponsor_class: [],
  intervention_type: [],
  sex: [],
  age_group: [],
  allocation: [],
  masking: [],
  primary_purpose: [],
  has_results: [],
  intervention_model: [],
  start_year: null,
  end_year: null,
  date_field: null,
  compare: null,
  exclude: { drug_name: [], condition: [], sponsor: [], country: [], term: [], status: [] },
};

export function quant(field: string, title: string, extra: Partial<QuantitativeChannel> = {}): QuantitativeChannel {
  return { field, type: "quantitative", title, unit: "trials", format: ",d", scale: "linear", ...extra };
}

export function category(field: string, title: string, domain: string[], extra: Partial<CategoryChannel> = {}): CategoryChannel {
  return { field, type: "nominal", title, domain, sort: null, is_exclusive: true, ...extra };
}

export function fieldDef(field: string, title: string, type: FieldDef["type"], extra: Partial<FieldDef> = {}): FieldDef {
  return { field, title, type, unit: null, format: null, href_field: null, ...extra };
}

/** Distinct, valid NCT IDs for the fixtures. */
export function nct(n: number): string {
  return `NCT${String(10000000 + n * 137).padStart(8, "0")}`;
}

export function cite(n: number, field = "protocolSection.designModule.phases", excerpt: string | null = "PHASE2"): Citation {
  return { nct_id: nct(n), field, excerpt };
}

export function datum(fields: Record<string, string | number | boolean | null>, citations: Citation[] = [], count?: number): Datum {
  return {
    ...fields,
    citations,
    citation_count: count ?? citations.length,
    source_url: `https://clinicaltrials.gov/api/v2/studies?query.cond=fixture&countTotal=true&pageSize=5&n=${citations[0]?.nct_id ?? "none"}`,
  };
}

/** The trials cited anywhere in `data`, as the response's `references`. */
export function referencesFor(...groups: Datum[][]): Record<string, TrialReference> {
  const out: Record<string, TrialReference> = {};
  for (const datum of groups.flat()) {
    for (const citation of datum.citations) {
      out[citation.nct_id] ??= {
        title: `Study of an investigational therapy (${citation.nct_id})`,
        url: `https://clinicaltrials.gov/study/${citation.nct_id}`,
        scope_evidence: [
          { entity_kind: "condition", entity_text: "fixture", field: "protocolSection.conditionsModule.conditions[0]", excerpt: "Fixture condition" },
        ],
      };
    }
  }
  return out;
}

export function makeMeta(query: string, overrides: Partial<Meta> = {}): Meta {
  return {
    request_id: "req-fixture-0001",
    generated_at: "2026-10-06T12:00:00Z",
    query,
    filters: emptyFilters,
    interpretation: null,
    plan: null,
    options: {},
    planner: {
      mode: "llm",
      model: "fixture-model",
      reasoning_effort: null,
      prompt_version: "v1",
      attempts: 1,
      is_repaired: false,
      is_fallback: false,
      usage: null,
    },
    assumptions: [],
    warnings: [],
    source: {
      name: "ClinicalTrials.gov",
      url: "https://clinicaltrials.gov",
      api_version: "2.0.5",
      data_timestamp: `${DATA_DATE}T09:00:00`,
      retrieved_at: "2026-10-06T12:00:00Z",
      study_url_template: "https://clinicaltrials.gov/study/{nct_id}",
      record_url_template: "https://clinicaltrials.gov/api/v2/studies/{nct_id}",
      fhir_url_template: "https://clinicaltrials.gov/api/v2/studies/{nct_id}?format=fhir.json",
      requests: [
        {
          method: "GET",
          url: "https://clinicaltrials.gov/api/v2/studies?query.cond=fixture&countTotal=true",
          status: 200,
          duration_ms: 140,
          total_count: 499,
          records_returned: 5,
          is_cached: false,
          origin: "execution",
        },
      ],
    },
    counts: null,
    truncation: { is_truncated: false, items: [] },
    citations: { is_enabled: true, max_per_datum: 5, selection: "most relevant trials by ClinicalTrials.gov's own ranking", trials_cited: 0 },
    suggested_followups: [],
    conversation: { is_follow_up: false, carried_over: [], changed: [] },
    cache: { is_plan_cached: false, is_response_cached: false, cached_at: null },
    timing: { total_ms: 1840, plan_ms: 900, resolve_ms: 200, fetch_ms: 600, build_ms: 140 },
    debug: {
      trace: [
        { index: 0, type: "plan", summary: "The model wrote a plan.", duration_ms: 900, request_indexes: [], detail: {} },
        { index: 1, type: "execute", summary: "Fetched one page.", duration_ms: 600, request_indexes: [0], detail: { pages: 1 } },
      ],
    },
    ...overrides,
  };
}

export function asRequest(query: string): QueryRequest {
  return { query };
}

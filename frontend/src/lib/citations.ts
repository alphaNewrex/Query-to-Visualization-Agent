/**
 * Helpers for the citation sheet (PLAN 6.5): citations grouped by trial, and links built from the
 * URL templates in `meta.source`. A template is filled only with a well-formed NCT ID, and a link
 * is used only when it is a web address, because the strings come from a third-party registry.
 */
import { isHttpUrl } from "./guards";
import type { AnalysisRequest, Citation, Datum, Meta, QueryResponse, Viz } from "./types";

/** How many trials "Show all" asks for: the most the backend allows per datum. */
export const FULL_CITATIONS = 100;

const RESERVED_KEYS = new Set(["citations", "citation_count", "source_url"]);

/** The request that runs an answer's own plan again with up to `FULL_CITATIONS` trials per datum; null when the answer has no plan. */
export function fullRequest(meta: Meta): AnalysisRequest | null {
  return meta.plan ? { plan: meta.plan, options: { ...meta.options, citations_per_datum: FULL_CITATIONS } } : null;
}

/** Every row of a chart that carries citations, in the order the response lists them: a network has its nodes, then its links. */
function datumRows(viz: Viz): readonly Datum[] {
  return viz.type === "network_graph" ? [...viz.data.nodes, ...viz.data.edges] : viz.data;
}

/** What names a datum: its own values (a row's fields, a node's id, a link's source and target), never its citations. */
function datumKey(datum: Datum): string {
  return JSON.stringify(
    Object.entries(datum)
      .filter(([name]) => !RESERVED_KEYS.has(name))
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );
}

/**
 * The datum of `before` as it appears in `after`, a run of the same plan; null when it cannot be told.
 * The row must have the same values and the same number of trials behind it: the same position is not
 * enough, because a data refresh in between can reorder or replace rows. Where several rows qualify,
 * the one at the old position wins; with none of them there, the match is ambiguous.
 */
export function findDatum(before: QueryResponse, datum: Datum, after: QueryResponse): Datum | null {
  const [first, second] = [before.visualization, after.visualization];
  if (!first || !second || first.type !== second.type) {
    return null;
  }
  const key = datumKey(datum);
  const rows = datumRows(second);
  const matches = rows.filter((row) => datumKey(row) === key && row.citation_count === datum.citation_count);
  const place = datumRows(first).indexOf(datum);
  const there = place >= 0 ? rows[place] : undefined;
  if (there && matches.includes(there)) {
    return there;
  }
  return matches.length === 1 ? matches[0] : null;
}

const NCT_ID = /^NCT\d{8}$/;

export interface TrialEvidence {
  nctId: string;
  citations: Citation[];
}

/** One group per cited trial, in the order each first appears; a link cites two fields of one trial. */
export function groupByTrial(citations: readonly Citation[]): TrialEvidence[] {
  const groups = new Map<string, TrialEvidence>();
  for (const citation of citations) {
    const group = groups.get(citation.nct_id);
    if (group) {
      group.citations.push(citation);
    } else {
      groups.set(citation.nct_id, { nctId: citation.nct_id, citations: [citation] });
    }
  }
  return [...groups.values()];
}

export function fillTemplate(template: string | null | undefined, nctId: string): string | null {
  if (!template || !NCT_ID.test(nctId)) {
    return null;
  }
  const url = template.replace("{nct_id}", encodeURIComponent(nctId));
  return isHttpUrl(url) ? url : null;
}

export interface TrialLinks {
  study: string | null;
  record: string | null;
  fhir: string | null;
}

export function trialLinks(nctId: string, source: Meta["source"], referenceUrl: string | undefined): TrialLinks {
  return {
    study: (isHttpUrl(referenceUrl) ? referenceUrl : null) ?? fillTemplate(source?.study_url_template ?? "https://clinicaltrials.gov/study/{nct_id}", nctId),
    record: fillTemplate(source?.record_url_template, nctId),
    fhir: fillTemplate(source?.fhir_url_template, nctId),
  };
}

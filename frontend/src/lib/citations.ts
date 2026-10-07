/**
 * Helpers for the citation sheet (PLAN 6.5): citations grouped by trial, and links built from the
 * URL templates in `meta.source`. A template is filled only with a well-formed NCT ID, and a link
 * is used only when it is a web address, because the strings come from a third-party registry.
 */
import { isHttpUrl } from "./guards";
import type { Citation, Meta } from "./types";

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

// One valid response per visualization type, plus the non-chart outcomes. Numbers follow PLAN
// section 8. Written by hand to the contract; nothing here comes from the backend.
import type { Datum, QueryResponse } from "@/lib/contract.gen";

import { asRequest, category, cite, datum, DATA_DATE, fieldDef, makeMeta, nct, quant, referencesFor } from "./build";

const SUB = `ClinicalTrials.gov, data as of ${DATA_DATE}`;

function visualization(
  message: string,
  viz: Extract<QueryResponse, { kind: "visualization" }>["visualization"],
  query: string,
  data: Datum[],
  meta: Partial<ReturnType<typeof makeMeta>> = {},
): QueryResponse {
  return {
    spec_version: "1.0",
    kind: "visualization",
    message,
    visualization: viz,
    clarification: null,
    references: referencesFor(data),
    meta: makeMeta(query, meta),
  };
}

// --- time series -------------------------------------------------------------------------------
const years = ["2015", "2016", "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"];
const perYear = [120, 195, 260, 255, 248, 263, 265, 299, 259, 256, 259, 220];
const tsData = years.map((year, i) =>
  datum({ start_year: year, trial_count: perYear[i] }, [cite(i * 3 + 1, "protocolSection.statusModule.startDateStruct.date", `${year}-03`), cite(i * 3 + 2, "protocolSection.statusModule.startDateStruct.date", `${year}-07-20`)], perYear[i]),
);
export const timeSeries: QueryResponse = visualization(
  "259 trials started in 2025; the peak was 299 in 2022.",
  {
    type: "time_series",
    title: "Trials started per year: pembrolizumab",
    subtitle: `Start years 2015 to 2026 · 2,899 trials · ${SUB}`,
    mark: "line",
    stack: "none",
    encoding: {
      x: { field: "start_year", type: "temporal", title: "Start year", time_unit: "year" },
      y: quant("trial_count", "Trials started"),
      series: null,
      tooltip: [],
    },
    data: tsData,
  },
  "How has the number of trials for pembrolizumab changed per year since 2015?",
  tsData,
  {
    filters: { ...makeMeta("").filters, drug_name: ["pembrolizumab"], start_year: 2015, date_field: "start_date" },
    assumptions: [
      "Matched 'pembrolizumab' with the registry's intervention search: 2,971 trials.",
      "'Per year' uses the study start date; planned (estimated) start dates are included.",
    ],
    warnings: [{ code: "partial_period", message: "2026 is incomplete: data as of 2026-10-06." }],
    suggested_followups: [{ label: "Split by phase", request: { query: "Pembrolizumab trials per year by phase", drug_name: ["pembrolizumab"] } }],
    counts: {
      data_points: 12,
      series: [{ label: null, trials_matched: 2906, trials_analyzed: 2899, trials_excluded: [{ reason: "planned_after_data_date", count: 7, message: "Planned start after 2026." }] }],
      trials_in_several_series: null,
    },
  },
);

// --- bar chart: single series ------------------------------------------------------------------
const phases = ["Early Phase 1", "Phase 1", "Phase 1/Phase 2", "Phase 2", "Phase 2/Phase 3", "Phase 3", "Phase 4", "Not Applicable", "No phase listed"];
const phaseCounts = [10, 49, 47, 88, 11, 50, 11, 91, 142];
const barData = phases.map((phase, i) => datum({ phase, trial_count: phaseCounts[i], share: phaseCounts[i] / 499 }, [cite(i + 40, "protocolSection.designModule.phases", phase), cite(i + 60)], phaseCounts[i]));
export const barChart: QueryResponse = visualization(
  "142 of 499 trials list no phase; Phase 2 is the largest phase with 88.",
  {
    type: "bar_chart",
    title: "Trials by phase: Duchenne muscular dystrophy",
    subtitle: `499 trials · ${SUB}`,
    orientation: "vertical",
    stack: "none",
    encoding: {
      x: category("phase", "Phase", phases, { type: "ordinal", sort: { by: "natural", order: "ascending" } }),
      y: quant("trial_count", "Trials"),
      series: null,
      tooltip: [fieldDef("share", "Share of trials", "quantitative", { format: ".1%" })],
    },
    data: barData,
  },
  "How are Duchenne muscular dystrophy trials distributed across phases?",
  barData,
);

// --- bar chart: horizontal ---------------------------------------------------------------------
const countries = ["China", "United States", "France", "Italy", "Germany", "Spain"];
const countryCounts = [903, 876, 245, 198, 187, 160];
const countryData = countries.map((country, i) => datum({ country, trial_count: countryCounts[i], iso_alpha3: ["CHN", "USA", "FRA", "ITA", "DEU", "ESP"][i] }, [cite(i + 80, "protocolSection.contactsLocationsModule.locations[0].country", country)], countryCounts[i]));
export const barHorizontal: QueryResponse = visualization(
  "China has the most recruiting lung cancer trials: 903.",
  {
    type: "bar_chart",
    title: "Recruiting trials by country: lung cancer",
    subtitle: `Top 6 of 77 countries · ${SUB}`,
    orientation: "horizontal",
    stack: "none",
    encoding: {
      x: category("country", "Country", countries, { sort: { by: "value", order: "descending" }, is_exclusive: false }),
      y: quant("trial_count", "Recruiting trials"),
      series: null,
      tooltip: [],
    },
    data: countryData,
  },
  "Which countries have the most recruiting trials for lung cancer?",
  countryData,
  { truncation: { is_truncated: true, items: [{ scope: "categories", shown: 6, total: 77, rule: "Top 6 countries by trial count." }] } },
);

// --- bar chart: grouped, with two series -------------------------------------------------------
const drugs = ["pembrolizumab", "nivolumab"];
const phasesShort = ["Phase 1", "Phase 2", "Phase 3", "Phase 4"];
const grouped = [[210, 140], [820, 560], [610, 430], [95, 60]];
const groupedData = phasesShort.flatMap((phase, i) =>
  drugs.map((group, j) => datum({ phase, group, trial_count: grouped[i][j], share: grouped[i][j] / (j ? 2029 : 2971) }, [cite(i * 2 + j + 100, "protocolSection.designModule.phases", phase.replace("Phase ", "PHASE"))], grouped[i][j])),
);
export const barGrouped: QueryResponse = visualization(
  "Phase 2 is the most common phase for both drugs.",
  {
    type: "bar_chart",
    title: "Trials by phase: pembrolizumab vs nivolumab",
    subtitle: `2,971 and 2,029 trials · ${SUB}`,
    orientation: "vertical",
    stack: "none",
    encoding: {
      x: category("phase", "Phase", phasesShort, { type: "ordinal", sort: { by: "natural", order: "ascending" } }),
      y: quant("trial_count", "Trials"),
      series: category("group", "Drug", drugs, { is_exclusive: false }),
      tooltip: [fieldDef("share", "Share of the drug's trials", "quantitative", { format: ".1%" })],
    },
    data: groupedData,
  },
  "Compare phases for trials involving pembrolizumab vs nivolumab.",
  groupedData,
  { assumptions: ["296 trials involve both drugs and appear in both series."] },
);

// --- network ----------------------------------------------------------------------------------
const sponsors = ["PTC Therapeutics", "Sarepta Therapeutics", "Pfizer", "Italfarmaco", "Santhera"];
const drugNames = ["Ataluren", "Eteplirsen", "Golodirsen", "Givinostat", "Idebenone", "Vamorolone"];
const sponsorCounts = [18, 14, 9, 6, 5];
const drugCounts = [13, 9, 6, 6, 5, 4];
const nodes = [
  ...sponsors.map((label, i) => datum({ id: `sponsor:${label}`, label, entity_type: "sponsor", trial_count: sponsorCounts[i] }, [cite(i + 120, "protocolSection.sponsorCollaboratorsModule.leadSponsor.name", label)], sponsorCounts[i])),
  ...drugNames.map((label, i) => datum({ id: `drug:${label}`, label, entity_type: "drug", trial_count: drugCounts[i] }, [cite(i + 130, "protocolSection.armsInterventionsModule.interventions[0].name", label)], drugCounts[i])),
] as Extract<NonNullable<QueryResponse["visualization"]>, { type: "network_graph" }>["data"]["nodes"];
const links: [number, number, number][] = [[0, 0, 13], [1, 1, 9], [1, 2, 6], [2, 5, 4], [3, 3, 6], [4, 4, 5], [0, 4, 2]];
const edges = links.map(([s, d, n], i) => ({
  ...datum({ trial_count: n }, [cite(i + 140, "protocolSection.sponsorCollaboratorsModule.leadSponsor.name", sponsors[s]), cite(i + 140, "protocolSection.armsInterventionsModule.interventions[0].name", drugNames[d])], n),
  id: `sponsor:${sponsors[s]}|drug:${drugNames[d]}`,
  source: `sponsor:${sponsors[s]}`,
  target: `drug:${drugNames[d]}`,
}));
export const network: QueryResponse = visualization(
  "PTC Therapeutics and ataluren are the strongest link: 13 trials.",
  {
    type: "network_graph",
    title: "Sponsors and the drugs they test: Duchenne muscular dystrophy",
    subtitle: `279 trials with a drug intervention · ${SUB}`,
    is_directed: false,
    layout: "bipartite",
    encoding: {
      nodes: {
        label: { field: "label" },
        color: category("entity_type", "Node type", ["sponsor", "drug"]),
        size: quant("trial_count", "Trials of this sponsor or drug"),
        tooltip: [],
      },
      edges: { weight: quant("trial_count", "Trials of this drug led by this sponsor"), tooltip: [] },
    },
    data: { nodes, edges },
  },
  "Show a network of sponsors and drugs for Duchenne muscular dystrophy trials.",
  [...nodes, ...edges],
  { truncation: { is_truncated: true, items: [{ scope: "nodes", shown: 11, total: 301, rule: "Top 15 per side." }] } },
);

// --- metric -----------------------------------------------------------------------------------
const metricData = [datum({ trial_count: 61 }, [cite(160, "protocolSection.statusModule.overallStatus", "RECRUITING"), cite(161, "protocolSection.statusModule.overallStatus", "RECRUITING")], 61)];
export const metric: QueryResponse = visualization(
  "61 trials are recruiting.",
  {
    type: "metric",
    title: "Recruiting trials: Duchenne muscular dystrophy",
    subtitle: `Status RECRUITING · ${SUB}`,
    encoding: { value: quant("trial_count", "Recruiting trials") },
    data: [metricData[0]],
  },
  "How many recruiting trials are there for Duchenne muscular dystrophy?",
  metricData,
);

// --- histogram --------------------------------------------------------------------------------
const bins: [number, number | null, string][] = [[0, 10, "0 to 9"], [10, 25, "10 to 24"], [25, 50, "25 to 49"], [50, 100, "50 to 99"], [100, 200, "100 to 199"], [200, 400, "200 to 399"], [400, 800, "400 to 799"], [800, null, "800 or more"]];
const binCounts = [132, 213, 462, 649, 492, 495, 246, 177];
const histData = bins.map(([start, end, label], i) => datum({ bin_start: start, bin_end: end, bin_label: label, trial_count: binCounts[i] }, [cite(i + 170, "protocolSection.designModule.enrollmentInfo.count", String(start + 5))], binCounts[i]));
export const histogram: QueryResponse = visualization(
  "Most trials enrol 50 to 99 participants.",
  {
    type: "histogram",
    title: "Enrollment sizes: pembrolizumab",
    subtitle: `2,866 trials · ${SUB}`,
    encoding: {
      x: quant("bin_start", "Enrollment", { unit: "participants" }),
      x2: { field: "bin_end" },
      y: quant("trial_count", "Trials"),
      label: { field: "bin_label" },
      tooltip: [],
    },
    data: histData,
  },
  "What is the distribution of enrollment sizes for pembrolizumab trials?",
  histData,
  { warnings: [{ code: "low_match_count", message: "5 trials have no enrollment count and are left out." }] },
);

// --- table ------------------------------------------------------------------------------------
const tableRows = [
  ["Pembrolizumab versus placebo after surgery in lung cancer", 1234, "PHASE3", "COMPLETED", "Merck Sharp & Dohme"],
  ["Chemotherapy with or without immunotherapy", 1100, "PHASE3", "RECRUITING", "National Cancer Institute"],
  ["Screening with low-dose CT", 950, "NA", "COMPLETED", "University Hospital"],
  ["Targeted therapy in EGFR-mutant disease", 870, "PHASE2", "ACTIVE_NOT_RECRUITING", "AstraZeneca"],
];
const tableData = tableRows.map(([title, enrollment, phase, status, sponsor], i) =>
  datum({ nct_id: nct(i + 200), title: String(title), url: `https://clinicaltrials.gov/study/${nct(i + 200)}`, enrollment: Number(enrollment), phase: String(phase), overall_status: String(status), lead_sponsor: String(sponsor) }, [cite(i + 200, "protocolSection.designModule.enrollmentInfo.count", String(enrollment))], 1),
);
export const table: QueryResponse = visualization(
  "The largest lung cancer trial enrols 1,234 participants.",
  {
    type: "table",
    title: "Largest lung cancer trials",
    subtitle: `Top 4 by enrollment · ${SUB}`,
    encoding: {
      columns: [
        fieldDef("nct_id", "NCT ID", "nominal", { href_field: "url" }),
        fieldDef("title", "Title", "nominal"),
        fieldDef("enrollment", "Enrollment", "quantitative", { unit: "participants", format: ",d" }),
        fieldDef("phase", "Phase", "nominal"),
        fieldDef("overall_status", "Status", "nominal"),
        fieldDef("lead_sponsor", "Lead sponsor", "nominal"),
      ],
    },
    data: tableData,
  },
  "List the 4 largest lung cancer trials.",
  tableData,
);

// --- scatter ----------------------------------------------------------------------------------
const scatterPoints: [string, number, number, string][] = [
  ["Phase 1", 24, 12, "a"], ["Phase 2", 60, 24, "b"], ["Phase 2", 120, 30, "c"], ["Phase 3", 300, 48, "d"],
  ["Phase 3", 900, 60, "e"], ["Phase 1", 12, 6, "f"], ["Phase 2", 45, 18, "g"], ["Phase 3", 150, 36, "h"],
];
const scatterData = scatterPoints.map(([phase, enrollment, months, id], i) =>
  datum({ nct_id: nct(i + 220), title: `Trial ${id}`, phase, enrollment, duration_months: months }, [cite(i + 220, "protocolSection.designModule.enrollmentInfo.count", String(enrollment))], 1),
);
export const scatter: QueryResponse = visualization(
  "Larger trials tend to run longer.",
  {
    type: "scatter_plot",
    title: "Enrollment against duration: completed Duchenne trials",
    subtitle: `8 trials · ${SUB}`,
    encoding: {
      x: quant("enrollment", "Enrollment", { unit: "participants", scale: "log" }),
      y: quant("duration_months", "Duration", { unit: "months", format: ".1f" }),
      series: category("phase", "Phase", ["Phase 1", "Phase 2", "Phase 3"], { type: "ordinal" }),
      size: null,
      label: fieldDef("title", "Trial", "nominal"),
      tooltip: [fieldDef("nct_id", "NCT ID", "nominal")],
    },
    data: scatterData,
  },
  "Plot enrollment against duration for completed Duchenne trials.",
  scatterData,
);

// --- non-chart outcomes -----------------------------------------------------------------------
export const clarification: QueryResponse = {
  spec_version: "1.0",
  kind: "clarification",
  message: "Which drug do you mean? Name it in the question or send `drug_name`.",
  visualization: null,
  clarification: {
    reason: "missing_entity",
    missing_fields: ["drug_name"],
    options: [
      { label: "Pembrolizumab", request: { query: "How has the number of trials changed over time?", drug_name: ["pembrolizumab"] } },
      { label: "Nivolumab", request: { query: "How has the number of trials changed over time?", drug_name: ["nivolumab"] } },
    ],
  },
  references: {},
  meta: makeMeta("How has the number of trials for this drug changed over time?", { source: null, debug: null }),
};

export const noData: QueryResponse = {
  spec_version: "1.0",
  kind: "no_data",
  message: "No trials matched the drug 'xyzzumab' (0 trials).",
  visualization: null,
  clarification: null,
  references: {},
  meta: makeMeta("How many trials per year for xyzzumab?", {
    suggested_followups: [{ label: "Try pembrolizumab", request: asRequest("How many trials per year for pembrolizumab?") }],
    counts: { data_points: 0, series: [{ label: null, trials_matched: 0, trials_analyzed: 0, trials_excluded: [] }], trials_in_several_series: null },
  }),
};

export const unsupported: QueryResponse = {
  spec_version: "1.0",
  kind: "unsupported",
  message: "This question cannot be answered from registry data.",
  visualization: null,
  clarification: null,
  references: {},
  meta: makeMeta("Which pembrolizumab trial is most likely to succeed?", { source: null }),
};

/** Every chart fixture by name, in the order the registry lists the types. */
export const chartFixtures: Record<string, QueryResponse> = {
  time_series: timeSeries,
  bar_chart: barChart,
  bar_horizontal: barHorizontal,
  bar_grouped: barGrouped,
  network_graph: network,
  metric,
  histogram,
  table,
  scatter_plot: scatter,
};

export const allFixtures: Record<string, QueryResponse> = { ...chartFixtures, clarification, no_data: noData, unsupported };

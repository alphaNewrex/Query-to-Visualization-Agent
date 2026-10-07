import type { ExampleSummary } from "./api";

/**
 * The assignment's sample questions, filled in with real entities. The page shows these when
 * GET /v1/examples is not available, and submits each as an ordinary query.
 */
export const BUILT_IN_EXAMPLES: ExampleSummary[] = [
  {
    slug: "trials-per-year",
    title: "Pembrolizumab trials per year",
    request: { query: "How has the number of trials for pembrolizumab changed per year since 2015?" },
  },
  {
    slug: "phases",
    title: "Duchenne trials by phase",
    request: { query: "How are Duchenne muscular dystrophy trials distributed across phases?" },
  },
  {
    slug: "compare-phases",
    title: "Pembrolizumab vs nivolumab",
    request: { query: "Compare phases for trials involving pembrolizumab vs nivolumab." },
  },
  {
    slug: "countries",
    title: "Countries recruiting for lung cancer",
    request: { query: "Which countries have the most recruiting trials for lung cancer?" },
  },
  {
    slug: "sponsor-drug-network",
    title: "Sponsors and drugs network",
    request: { query: "Show a network of sponsors and drugs for Duchenne muscular dystrophy trials." },
  },
  {
    slug: "drug-cooccurrence",
    title: "Drugs combined with pembrolizumab",
    request: { query: "Which drugs frequently co-occur in combination studies with pembrolizumab?" },
  },
  {
    slug: "enrollment",
    title: "Enrollment sizes",
    request: { query: "What is the distribution of enrollment sizes for pembrolizumab trials?" },
  },
  {
    slug: "recruiting-count",
    title: "Recruiting Duchenne trials",
    request: { query: "How many recruiting trials are there for Duchenne muscular dystrophy?" },
  },
];

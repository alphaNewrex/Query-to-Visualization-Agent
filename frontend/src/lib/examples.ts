import type { ExampleListing } from "./api";

/** A recorded run as the gallery lists it. */
export interface ExampleChip extends ExampleListing {
  /** Short, and different from the label of every other chip. */
  label: string;
}

/** "02-compare-phases" becomes "Compare phases": the slug is the recording's name, and its number only the order. */
function labelFromSlug(slug: string): string {
  const words = slug.replace(/^\d+-/, "").split("-").join(" ").trim();
  return words === "" ? slug : words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Labels for the chips. The recorded question cannot be the label: two recordings can share it,
 * because one carries a structured field (`drug_name`) and the other does not.
 */
export function withLabels(listing: readonly ExampleListing[]): ExampleChip[] {
  const labels = listing.map((item) => labelFromSlug(item.slug));
  return listing.map((item, index) => ({
    ...item,
    // Should two slugs still give the same words, the number in front keeps them apart.
    label: labels.filter((label) => label === labels[index]).length > 1 ? item.slug : labels[index],
  }));
}

/** "time_series" becomes "time series", "no_data" becomes "no data". */
export function outcomeName(outcome: string | null): string {
  return (outcome ?? "").split("_").join(" ");
}

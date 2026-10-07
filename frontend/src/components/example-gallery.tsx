"use client";

import * as React from "react";
import {
  BanIcon,
  ChartColumnIcon,
  ChartLineIcon,
  ChartNoAxesColumnIcon,
  ChartScatterIcon,
  CircleHelpIcon,
  HashIcon,
  NetworkIcon,
  PlayIcon,
  SearchXIcon,
  TableIcon,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getExamples, isAbortError } from "@/lib/api";
import { type ExampleChip, outcomeName, withLabels } from "@/lib/examples";
import { utcMinute } from "@/lib/format";

/** One icon for each thing a recording can show: the seven chart types and the three outcomes without a chart. */
const ICONS: Record<string, LucideIcon> = {
  time_series: ChartLineIcon,
  bar_chart: ChartColumnIcon,
  histogram: ChartNoAxesColumnIcon,
  scatter_plot: ChartScatterIcon,
  network_graph: NetworkIcon,
  table: TableIcon,
  metric: HashIcon,
  clarification: CircleHelpIcon,
  no_data: SearchXIcon,
  unsupported: BanIcon,
};

/**
 * The recorded runs of GET /v1/examples as chips (PLAN 6.2). A chip is only a choice: the page
 * fetches the recording and shows it.
 */
export function ExampleGallery({ selected, onSelect }: { selected: string | null; onSelect: (slug: string) => void }) {
  const [chips, setChips] = React.useState<ExampleChip[] | "loading" | "failed">("loading");

  React.useEffect(() => {
    const controller = new AbortController();
    getExamples(controller.signal)
      .then((listing) => setChips(withLabels(listing)))
      .catch((error: unknown) => {
        if (!isAbortError(error)) {
          setChips("failed");
        }
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="flex flex-col gap-2" role="group" aria-labelledby="gallery-title">
      <span id="gallery-title" className="text-xs font-medium text-muted-foreground">
        Recorded examples: each answers at once, without a model
      </span>
      {chips === "loading" ? (
        <div className="flex flex-wrap gap-1.5" aria-hidden>
          {[28, 36, 32, 40, 30, 34].map((width) => (
            <Skeleton key={width} className="h-7 rounded-lg" style={{ width: `${width * 4}px` }} />
          ))}
        </div>
      ) : null}
      {chips === "failed" ? <p className="text-sm text-muted-foreground">The recorded examples could not be loaded.</p> : null}
      {Array.isArray(chips) && chips.length === 0 ? (
        <p className="text-sm text-muted-foreground">This server has no recorded examples.</p>
      ) : null}
      {Array.isArray(chips) && chips.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((chip) => {
            const Icon = chip.outcome ? ICONS[chip.outcome] : undefined;
            const isSelected = chip.slug === selected;
            return (
              <Button
                key={chip.slug}
                variant={isSelected ? "secondary" : "outline"}
                size="sm"
                aria-pressed={isSelected}
                title={chip.query}
                className="max-w-full"
                onClick={() => onSelect(chip.slug)}
              >
                {Icon ? <Icon aria-hidden /> : null}
                <span className="truncate">{chip.label}</span>
                <span className="sr-only">, {outcomeName(chip.outcome)}</span>
              </Button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/** How the answer on show came about. */
export type Source = "recorded" | "live" | "live-recorded-plan" | "live-same-plan";

const SOURCES: Record<Source, { badge: string; note: string }> = {
  recorded: { badge: "Recorded", note: "Shown without calling the model or ClinicalTrials.gov." },
  live: { badge: "Live", note: "Answered just now from ClinicalTrials.gov." },
  "live-recorded-plan": {
    badge: "Live data, recorded plan",
    note: "The recorded plan ran against ClinicalTrials.gov just now. No model was used.",
  },
  "live-same-plan": {
    badge: "Live data, same plan",
    note: "The plan of the answer before ran against ClinicalTrials.gov just now. No model was used.",
  },
};

/**
 * Says whether the answer is recorded or live, and offers "Run live" next to a recorded one. With
 * no model on the server, "Run live" runs the recorded plan on live data (PLAN 6.2).
 */
export function SourceBar({
  source,
  recordedAt,
  noPlanner,
  onRunLive,
  busy,
}: {
  source: Source;
  /** When the answer was made (`meta.generated_at`): for a recording, the day it was recorded. */
  recordedAt: string;
  /** The server has no model: "Run live" executes the recorded plan. */
  noPlanner: boolean;
  /** Only a recorded answer can be run live. */
  onRunLive: (() => void) | null;
  busy: boolean;
}) {
  const { badge, note } = SOURCES[source];
  const hint = noPlanner
    ? "This server has no model, so Run live runs the recorded plan on live data."
    : "Run live asks the same question again.";
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-muted/30 px-3 py-2" data-testid="source-bar">
      <Badge variant={source === "recorded" ? "secondary" : "outline"}>{badge}</Badge>
      <p className="min-w-0 flex-1 basis-56 text-sm text-muted-foreground">
        {source === "recorded" ? `Recorded ${utcMinute(recordedAt)}. ` : null}
        {note}
        {onRunLive ? ` ${hint}` : null}
      </p>
      {onRunLive ? (
        <Button size="sm" onClick={onRunLive} disabled={busy}>
          <PlayIcon aria-hidden />
          Run live
        </Button>
      ) : null}
    </div>
  );
}

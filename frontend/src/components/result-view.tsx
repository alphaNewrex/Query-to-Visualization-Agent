"use client";

import * as React from "react";
import { CheckIcon, CopyIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FallbackView } from "@/components/viz/fallback";
import { DataTable } from "@/components/viz/table-view";
import { VisualizationView } from "@/components/viz/registry";
import { dataTablesFor, rowName, rowValue } from "@/lib/data-tables";
import { classify } from "@/lib/guards";
import type { AnalysisRequest, DatumSelection, QueryRequest, QueryResponse, Viz } from "@/lib/types";

import { ErrorBoundary } from "./error-boundary";
import { Notes } from "./notes";
import { OutcomeCard } from "./outcome-card";
import { TraceTab } from "./trace-tab";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      variant="outline"
      size="xs"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <CheckIcon aria-hidden /> : <CopyIcon aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function DataTab({ visualization, onSelect }: { visualization: Viz; onSelect: (selection: DatumSelection) => void }) {
  return (
    <div className="flex flex-col gap-6">
      {dataTablesFor(visualization).map((spec, index) => (
        <div key={index} className="flex flex-col gap-2">
          {spec.title ? <h3 className="text-sm font-medium">{spec.title}</h3> : null}
          <div className="overflow-x-auto">
            <DataTable
              columns={spec.columns}
              rows={spec.rows}
              rowLabel={(row, i) => rowName(spec.columns, row, i)}
              rowValue={(row) => rowValue(spec.measure, row)}
              onSelect={onSelect}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

/** One answer: title, message and the four tabs; or the card of a non-chart outcome. */
export function ResultView({
  response,
  request,
  onSelect,
  onRun,
  onRerun,
  busy,
}: {
  response: QueryResponse;
  /** What was sent for this answer: a question, or a plan that ran without a model. */
  request: QueryRequest | AnalysisRequest | null;
  onSelect: (selection: DatumSelection) => void;
  onRun: (request: QueryRequest) => void;
  onRerun?: () => void;
  busy?: boolean;
}) {
  const outcome = classify(response);

  if (outcome.tag === "clarification" || outcome.tag === "message") {
    return <OutcomeCard response={outcome.response} onRun={onRun} />;
  }

  const visualization = outcome.tag === "chart" ? outcome.visualization : null;
  const json = JSON.stringify({ request, response }, null, 2);
  const fallback = (reason?: string) => <FallbackView response={response} reason={reason} />;

  return (
    <article className="flex flex-col gap-4" data-testid="result">
      <header className="flex flex-col gap-1">
        {visualization ? <h2 className="text-xl font-semibold tracking-tight break-words">{visualization.title}</h2> : null}
        {visualization?.subtitle ? <p className="text-sm text-muted-foreground">{visualization.subtitle}</p> : null}
        <p className="text-base break-words">{response.message}</p>
      </header>
      <Tabs defaultValue="chart">
        <TabsList>
          <TabsTrigger value="chart">Chart</TabsTrigger>
          <TabsTrigger value="data" disabled={visualization === null}>
            Data
          </TabsTrigger>
          <TabsTrigger value="trace">Trace</TabsTrigger>
          <TabsTrigger value="json">JSON</TabsTrigger>
        </TabsList>
        <TabsContent value="chart" className="pt-4">
          {visualization ? (
            <ErrorBoundary
              resetKey={response}
              fallback={(error) => fallback(`The chart could not be drawn (${error.message}).`)}
            >
              <VisualizationView spec={visualization} onSelect={onSelect} />
            </ErrorBoundary>
          ) : (
            fallback()
          )}
          <Notes meta={response.meta} onRun={onRun} />
        </TabsContent>
        <TabsContent value="data" className="pt-4">
          {visualization ? (
            <ErrorBoundary resetKey={response} fallback={() => fallback()}>
              <DataTab visualization={visualization} onSelect={onSelect} />
            </ErrorBoundary>
          ) : null}
        </TabsContent>
        <TabsContent value="trace" className="pt-4">
          <TraceTab meta={response.meta} onRerun={onRerun} disabled={busy} />
        </TabsContent>
        <TabsContent value="json" className="pt-4">
          <div className="flex flex-col items-start gap-2">
            <CopyButton text={json} />
            <pre className="max-h-[32rem] w-full overflow-auto rounded-lg border bg-muted/30 p-3 text-xs">{json}</pre>
          </div>
        </TabsContent>
      </Tabs>
    </article>
  );
}

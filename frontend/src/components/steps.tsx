"use client";

import { CheckIcon, XIcon } from "lucide-react";

import { type Step, stepLabel } from "@/lib/conversation";
import { cn } from "@/lib/utils";

import { WaveLoader } from "./wave-loader";

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "started") {
    return <WaveLoader size={16} decorative className="shrink-0" />;
  }
  if (status === "failed") {
    return <XIcon className="size-4 text-destructive" aria-hidden />;
  }
  return <CheckIcon className="size-4 text-muted-foreground" aria-hidden />;
}

/** The steps the agent has taken so far, each with a spinner while it runs and a check once it is done. */
export function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm" data-testid="steps">
      {steps.map((step) => (
        <li key={step.step} className={cn("flex items-center gap-2", step.status === "started" ? "text-foreground" : "text-muted-foreground")}>
          <StepIcon status={step.status} />
          <span className="min-w-0 break-words">{stepLabel(step)}</span>
          <span className="sr-only">{step.status === "started" ? " (running)" : step.status === "failed" ? " (stopped)" : " (done)"}</span>
        </li>
      ))}
    </ol>
  );
}

/** The live view of a running turn. */
export function RunningSteps({ steps }: { steps: Step[] }) {
  return (
    <div role="status" aria-live="polite" aria-label="The agent is working" className="rounded-lg border bg-muted/30 px-3 py-2.5">
      {steps.length > 0 ? <StepList steps={steps} /> : <StepList steps={[{ step: "plan", status: "started", summary: "" }]} />}
    </div>
  );
}

/** What is left of the steps when the answer is there: closed until the reader asks. */
export function StepsDisclosure({ steps }: { steps: Step[] }) {
  if (steps.length === 0) {
    return null;
  }
  return (
    <details className="group text-sm" data-testid="steps-disclosure">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-md text-xs text-muted-foreground outline-none select-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <span className="group-open:hidden">Show steps</span>
        <span className="hidden group-open:inline">Hide steps</span>
        <span className="text-muted-foreground/70">({steps.length})</span>
      </summary>
      <div className="mt-2 rounded-lg border bg-muted/30 px-3 py-2.5">
        <StepList steps={steps} />
      </div>
    </details>
  );
}

"use client";

import { Badge } from "@/components/ui/badge";
import { classify } from "@/lib/guards";
import { conversationOf, type Turn } from "@/lib/conversation";
import type { DatumSelection, Meta, QueryRequest } from "@/lib/types";

import { SourceBar } from "./example-gallery";
import { Followups } from "./notes";
import { ErrorCard } from "./outcome-card";
import { ResultSkeleton } from "./result-skeleton";
import { ResultView } from "./result-view";
import { RunningSteps, StepsDisclosure } from "./steps";

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[88%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2 text-sm break-words whitespace-pre-wrap text-primary-foreground sm:max-w-[75%]">
        <span className="sr-only">You: </span>
        {text}
      </p>
    </div>
  );
}

/** What the backend understood of a follow-up: what it kept from the answer before and what the question changed. */
function Understood({ meta }: { meta: Meta }) {
  const info = conversationOf(meta);
  if (!info) {
    return null;
  }
  const kept = info.is_follow_up ? info.carried_over : [];
  const changed = info.is_follow_up ? info.changed : [];
  if (kept.length === 0 && changed.length === 0) {
    return null;
  }
  return (
    <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label="What was understood" data-testid="understood">
      {kept.map((item, index) => (
        <li key={`k${index}`}>
          <Badge variant="secondary" className="h-auto min-h-5 max-w-full whitespace-normal py-0.5">
            <span className="font-normal opacity-70">Kept:</span> {item}
          </Badge>
        </li>
      ))}
      {changed.map((item, index) => (
        <li key={`c${index}`}>
          <Badge variant="outline" className="h-auto min-h-5 max-w-full whitespace-normal py-0.5">
            <span className="font-normal opacity-70">Changed:</span> {item}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

/** One answer of the agent: its steps, what it understood, and the answer with its follow-ups. */
export function AssistantTurn({
  turn,
  busy,
  noPlanner,
  onSelect,
  onRun,
  onRunLive,
  onRerun,
  onRetry,
}: {
  turn: Turn;
  /** Some turn is running: the actions that start another one wait. */
  busy: boolean;
  noPlanner: boolean;
  onSelect: (turnId: number, selection: DatumSelection) => void;
  onRun: (request: QueryRequest, label?: string) => void;
  onRunLive: (turn: Turn) => void;
  onRerun: (turn: Turn) => void;
  onRetry: (turn: Turn) => void;
}) {
  const { state } = turn;
  return (
    <section className="flex flex-col gap-3" aria-label="Answer" aria-busy={state.tag === "running"}>
      {state.tag === "running" ? (
        turn.streaming ? (
          <RunningSteps steps={turn.steps} />
        ) : (
          <ResultSkeleton caption="Planning, fetching from ClinicalTrials.gov and building the chart. This can take up to a minute." />
        )
      ) : null}

      {state.tag === "error" ? (
        <>
          <StepsDisclosure steps={turn.steps} />
          <ErrorCard error={state.error} onRetry={() => onRetry(turn)} />
        </>
      ) : null}

      {state.tag === "done" ? (
        <>
          <StepsDisclosure steps={turn.steps} />
          <Understood meta={state.response.meta} />
          {state.source !== "live" || state.example ? (
            <SourceBar
              source={state.source}
              recordedAt={state.response.meta.generated_at}
              noPlanner={noPlanner}
              onRunLive={state.source === "recorded" && state.example ? () => onRunLive(turn) : null}
              busy={busy}
            />
          ) : null}
          <ResultView
            response={state.response}
            request={state.sent}
            onSelect={(selection) => onSelect(turn.id, selection)}
            onRun={onRun}
            onRerun={state.response.meta.plan ? () => onRerun(turn) : undefined}
            busy={busy}
            hideFollowups
          />
          {classify(state.response).tag === "chart" || classify(state.response).tag === "fallback" ? <Followups items={state.response.meta.suggested_followups} onRun={onRun} /> : null}
        </>
      ) : null}
    </section>
  );
}

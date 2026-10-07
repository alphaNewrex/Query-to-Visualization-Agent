"use client";

import { AlertTriangleIcon, HelpCircleIcon, SearchXIcon, BanIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import type { ClarificationResponse, MessageResponse, QueryRequest } from "@/lib/types";

import { Followups } from "./notes";

/** Clarification, no data and unsupported (a conversation reply has its own, plain, component): all HTTP 200 answers that carry no chart. */
export function OutcomeCard({
  response,
  onRun,
}: {
  response: ClarificationResponse | MessageResponse;
  onRun: (request: QueryRequest, label?: string) => void;
}) {
  const clarification = response.kind === "clarification" ? response.clarification : null;
  const Icon = clarification ? HelpCircleIcon : response.kind === "no_data" ? SearchXIcon : BanIcon;
  const title = clarification ? "More detail needed" : response.kind === "no_data" ? "No data" : "Not supported";

  return (
    <Alert data-outcome={response.kind}>
      <Icon aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col gap-3">
        <p>{response.message}</p>
        {clarification && clarification.options.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {clarification.options.map((option, index) => (
              <Button key={index} variant="outline" size="sm" onClick={() => onRun(option.request, option.label)}>
                {option.label}
              </Button>
            ))}
          </div>
        ) : null}
        {response.meta.suggested_followups.length > 0 ? <Followups items={response.meta.suggested_followups} onRun={onRun} /> : null}
      </AlertDescription>
    </Alert>
  );
}

/** A greeting, thanks or a question about the service: the reply is a plain message, with questions to click. */
export function ConversationReply({
  response,
  onRun,
}: {
  response: MessageResponse;
  onRun: (request: QueryRequest, label?: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3" data-outcome="conversation">
      <p className="max-w-[88%] rounded-2xl rounded-bl-md bg-muted px-3.5 py-2 text-sm break-words sm:max-w-[75%]">{response.message}</p>
      <Followups items={response.meta.suggested_followups} onRun={onRun} />
    </div>
  );
}

export function ErrorCard({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <Alert variant="destructive" data-outcome="error">
      <AlertTriangleIcon aria-hidden />
      <AlertTitle>{api?.code === "stopped" ? "Stopped" : api ? "The request failed" : "Something went wrong"}</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <p>{error.message}</p>
        {api?.code === "planner_unavailable" ? (
          <p>This server has no model, so it cannot read a typed question. The recorded examples need none: start a new conversation to pick one.</p>
        ) : null}
        <p className="font-mono text-xs">
          {api ? `${api.code}${api.status ? ` · HTTP ${api.status}` : ""}` : null}
          {api?.requestId ? ` · request ${api.requestId}` : null}
        </p>
        {api?.isRetryable && onRetry ? (
          <div>
            <Button variant="outline" size="sm" onClick={onRetry}>
              Try again
            </Button>
          </div>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

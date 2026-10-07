"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { type ExampleSummary, getExamples } from "@/lib/api";
import { BUILT_IN_EXAMPLES } from "@/lib/examples";
import type { QueryRequest } from "@/lib/types";

/** Example questions from GET /v1/examples, or the built-in list when that endpoint is not there. */
export function ExampleChips({ onRun, disabled }: { onRun: (request: QueryRequest) => void; disabled: boolean }) {
  const [examples, setExamples] = React.useState<ExampleSummary[]>(BUILT_IN_EXAMPLES);

  React.useEffect(() => {
    const controller = new AbortController();
    getExamples(controller.signal)
      .then((found) => found.length > 0 && setExamples(found))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium text-muted-foreground">Try an example</span>
      <div className="flex flex-wrap gap-1.5">
        {examples.map((example) => (
          <Button key={example.slug} variant="outline" size="sm" disabled={disabled} onClick={() => onRun(example.request)}>
            {example.title}
          </Button>
        ))}
      </div>
    </div>
  );
}

"use client";

import { QuoteIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cellNumber } from "@/lib/cell";
import { formatNumber, MISSING } from "@/lib/format";

import { selection } from "./chart-kit";
import type { RendererProps } from "./renderer-props";

/** One number with its unit. The row still cites the trials behind it. */
export function MetricView({ spec, onSelect }: RendererProps<"metric">) {
  const channel = spec.encoding.value;
  const datum = spec.data[0];
  if (datum === undefined) {
    return <p className="text-sm text-muted-foreground">The answer has no value.</p>;
  }
  const value = cellNumber(datum, channel.field);

  return (
    <Card data-mark="metric" className="max-w-md">
      <CardContent className="flex flex-col items-start gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-sm text-muted-foreground">{channel.title}</span>
          <span className="text-6xl leading-none font-semibold tracking-tight">
            {value === null ? MISSING : formatNumber(value, channel.format)}
          </span>
          {channel.unit ? <span className="text-sm text-muted-foreground">{channel.unit}</span> : null}
        </div>
        <Button variant="outline" size="sm" onClick={() => onSelect(selection(channel.title, datum, channel))}>
          <QuoteIcon aria-hidden />
          Show the trials ({datum.citation_count.toLocaleString("en-US")})
        </Button>
      </CardContent>
    </Card>
  );
}

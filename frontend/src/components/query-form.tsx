"use client";

import * as React from "react";
import { ChevronDownIcon, SendIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  type FormErrors,
  type FormValues,
  inputId,
  PHASES,
  STATUSES,
  STRUCTURED_FIELDS,
  statusLabel,
  TEXT_FIELDS,
  validate,
} from "@/lib/request-form";
import { cn } from "@/lib/utils";

export interface FocusRequest {
  fields: string[];
  nonce: number;
}

export function QueryForm({
  values,
  onChange,
  onSubmit,
  busy,
  focus,
  reveal,
}: {
  values: FormValues;
  onChange: (values: FormValues) => void;
  onSubmit: () => void;
  busy: boolean;
  focus: FocusRequest | null;
  /** Changes whenever a request that fills a structured field is put into the form: the block opens to show it. */
  reveal: number;
}) {
  const [open, setOpen] = React.useState(false);
  const [errors, setErrors] = React.useState<FormErrors>({});
  const [lastNonce, setLastNonce] = React.useState(0);
  const [lastReveal, setLastReveal] = React.useState(0);

  if (reveal !== lastReveal) {
    setLastReveal(reveal);
    setOpen(true);
  }

  // A clarification names the missing fields: open the block when one is in it, then focus the first.
  if (focus && focus.nonce !== lastNonce) {
    setLastNonce(focus.nonce);
    if (focus.fields.some((field) => STRUCTURED_FIELDS.has(field))) {
      setOpen(true);
    }
  }
  React.useEffect(() => {
    if (!focus || focus.fields.length === 0) {
      return;
    }
    const target = focus.fields.map((field) => document.getElementById(inputId(field))).find(Boolean);
    target?.focus();
  }, [focus, open]);

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) => onChange({ ...values, [key]: value });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const found = validate(values);
    setErrors(found);
    if (found.start_year || found.end_year) {
      setOpen(true);
    }
    if (Object.keys(found).length === 0) {
      onSubmit();
    }
  };

  const selectClass =
    "h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
      <label htmlFor="field-query" className="sr-only">
        Your question about clinical trials
      </label>
      <Textarea
        id="field-query"
        value={values.query}
        onChange={(event) => set("query", event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.currentTarget.form?.requestSubmit();
          }
        }}
        placeholder="Ask about clinical trials, for example: How has the number of trials for pembrolizumab changed per year since 2015?"
        rows={3}
        aria-invalid={errors.query ? true : undefined}
        className="min-h-20 resize-y text-base md:text-sm"
      />
      {errors.query ? <p className="text-xs text-destructive">{errors.query}</p> : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button type="button" variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} aria-hidden />
          Structured fields
        </Button>
        <Button type="submit" disabled={busy}>
          <SendIcon aria-hidden />
          {busy ? "Running" : "Ask"}
        </Button>
      </div>

      {open ? (
        <fieldset className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
          <legend className="px-1 text-xs text-muted-foreground">Optional: fix a value instead of leaving it to the question</legend>
          {TEXT_FIELDS.map((field) => (
            <label key={field.name} className="flex flex-col gap-1 text-xs font-medium">
              {field.label}
              <Input
                id={inputId(field.name)}
                value={values[field.name]}
                placeholder={field.placeholder}
                onChange={(event) => set(field.name, event.target.value)}
              />
            </label>
          ))}
          <label className="flex flex-col gap-1 text-xs font-medium">
            Status
            <select
              id={inputId("status")}
              className={selectClass}
              value={values.status}
              onChange={(event) => set("status", event.target.value as FormValues["status"])}
            >
              <option value="">Any</option>
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {statusLabel(status)}
                </option>
              ))}
            </select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-xs font-medium">
              From year
              <Input
                id={inputId("start_year")}
                inputMode="numeric"
                value={values.start_year}
                placeholder="2015"
                aria-invalid={errors.start_year ? true : undefined}
                onChange={(event) => set("start_year", event.target.value)}
              />
              {errors.start_year ? <span className="font-normal text-destructive">{errors.start_year}</span> : null}
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium">
              To year
              <Input
                id={inputId("end_year")}
                inputMode="numeric"
                value={values.end_year}
                placeholder="2025"
                aria-invalid={errors.end_year ? true : undefined}
                onChange={(event) => set("end_year", event.target.value)}
              />
              {errors.end_year ? <span className="font-normal text-destructive">{errors.end_year}</span> : null}
            </label>
          </div>
          <div id={inputId("trial_phase")} tabIndex={-1} className="flex flex-col gap-1 text-xs font-medium sm:col-span-2">
            Phase
            <div className="flex flex-wrap gap-1.5">
              {PHASES.map((phase) => {
                const on = values.trial_phase.includes(phase.value);
                return (
                  <Button
                    key={phase.value}
                    type="button"
                    size="xs"
                    variant={on ? "default" : "outline"}
                    aria-pressed={on}
                    onClick={() =>
                      set("trial_phase", on ? values.trial_phase.filter((item) => item !== phase.value) : [...values.trial_phase, phase.value])
                    }
                  >
                    {phase.label}
                  </Button>
                );
              })}
            </div>
          </div>
        </fieldset>
      ) : null}
    </form>
  );
}

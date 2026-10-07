"use client";

import * as React from "react";
import { ArrowUpIcon, ChevronDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  type FormErrors,
  type FormValues,
  inputId,
  PHASES,
  STATUSES,
  statusLabel,
  TEXT_FIELDS,
  validate,
} from "@/lib/request-form";
import { cn } from "@/lib/utils";

import { WaveLoader } from "./wave-loader";

export function QueryForm({
  values,
  onChange,
  onSubmit,
  onStop,
  busy,
  focusKey,
  placeholder,
}: {
  values: FormValues;
  onChange: (values: FormValues) => void;
  onSubmit: () => void;
  /** Stops the turn that is running; the send button becomes a stop button while `busy`. */
  onStop: () => void;
  busy: boolean;
  /** Changes whenever the composer should take the focus, for instance after a clarification. */
  focusKey: number;
  placeholder: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [errors, setErrors] = React.useState<FormErrors>({});
  const textarea = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (focusKey > 0) {
      textarea.current?.focus();
    }
  }, [focusKey]);

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) => onChange({ ...values, [key]: value });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const found = validate(values);
    setErrors(found);
    if (found.start_year || found.end_year) {
      setOpen(true);
    }
    if (Object.keys(found).length === 0) {
      setErrors({});
      onSubmit();
    }
  };

  const selectClass =
    "h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" noValidate aria-label="Ask a question">
      <div className="flex items-end gap-2">
        <label htmlFor="field-query" className="sr-only">
          Your question about clinical trials
        </label>
        <Textarea
          ref={textarea}
          id="field-query"
          value={values.query}
          onChange={(event) => set("query", event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line; Enter that confirms an IME composition does neither.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={placeholder}
          rows={1}
          aria-invalid={errors.query ? true : undefined}
          aria-describedby="composer-hint"
          className="max-h-40 min-h-10 resize-none text-base md:text-sm"
        />
        {busy ? (
          <Button type="button" size="icon-lg" variant="outline" onClick={onStop} aria-label="Stop">
            <WaveLoader size={16} decorative />
          </Button>
        ) : (
          <Button type="submit" size="icon-lg" aria-label="Ask">
            <ArrowUpIcon aria-hidden />
          </Button>
        )}
      </div>
      {errors.query ? <p className="text-xs text-destructive">{errors.query}</p> : null}

      <div className="flex flex-wrap items-center justify-between gap-x-3">
        <Button type="button" variant="ghost" size="xs" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} aria-hidden />
          Structured fields
        </Button>
        <span id="composer-hint" className="hidden text-xs text-muted-foreground sm:inline">
          Enter to send, Shift+Enter for a new line
        </span>
      </div>

      {open ? (
        <fieldset className="grid max-h-[40dvh] gap-3 overflow-y-auto rounded-lg border p-3 sm:grid-cols-2">
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

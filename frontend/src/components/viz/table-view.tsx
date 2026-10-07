"use client";

import * as React from "react";
import { QuoteIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cell, cellNumber, cellText } from "@/lib/cell";
import { isHttpUrl } from "@/lib/guards";
import { formatCellValue, formatWithUnit } from "@/lib/format";
import type { Datum, DatumSelection, FieldDef } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { RendererProps } from "./renderer-props";

export interface DataTableProps {
  columns: readonly FieldDef[];
  rows: readonly Datum[];
  /** How a row is named in the citation sheet and in its button's accessible name. */
  rowLabel: (row: Datum, index: number) => string;
  /** The value shown beside that name in the sheet. */
  rowValue: (row: Datum) => string;
  /** Without this the table has no citation buttons. */
  onSelect?: (selection: DatumSelection) => void;
  caption?: string;
}

/**
 * The rows as a table: the view of the `table` type, of the Data tab and of the fallback. A row is
 * a click target, and so is its "Citations" button, which keeps citations within reach of the
 * keyboard.
 */
export function DataTable({ columns, rows, rowLabel, rowValue, onSelect, caption }: DataTableProps) {
  const select = (row: Datum, index: number) => onSelect?.({ label: rowLabel(row, index), value: rowValue(row), datum: row });

  return (
    <Table>
      {caption ? <caption className="sr-only">{caption}</caption> : null}
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {columns.map((column) => (
            <TableHead key={column.field} className={cn(column.type === "quantitative" && "text-right")}>
              {column.title}
            </TableHead>
          ))}
          {onSelect ? <TableHead className="w-px text-right">Citations</TableHead> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) => (
          <TableRow
            key={index}
            data-mark="row"
            className={cn(onSelect && "cursor-pointer")}
            onClick={onSelect ? () => select(row, index) : undefined}
          >
            {columns.map((column) => (
              <TableCell
                key={column.field}
                className={cn(
                  column.type === "quantitative" ? "text-right tabular-nums" : "max-w-[26rem] whitespace-normal",
                )}
              >
                <CellContent row={row} column={column} />
              </TableCell>
            ))}
            {onSelect ? (
              <TableCell className="text-right">
                <Button
                  variant="ghost"
                  size="xs"
                  aria-label={`Citations for ${rowLabel(row, index)}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    select(row, index);
                  }}
                >
                  <QuoteIcon aria-hidden />
                  {row.citation_count}
                </Button>
              </TableCell>
            ) : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function CellContent({ row, column }: { row: Datum; column: FieldDef }) {
  const value = cell(row, column.field);
  const text = formatCellValue(value, column);
  const href = column.href_field ? cell(row, column.href_field) : null;
  if (isHttpUrl(href)) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline underline-offset-3 hover:no-underline"
        onClick={(event) => event.stopPropagation()}
      >
        {text}
      </a>
    );
  }
  return <>{text}</>;
}

/** A trial list: one row per trial, columns as the encoding gives them. */
export function TableView({ spec, onSelect }: RendererProps<"table">) {
  const { columns } = spec.encoding;
  // A trial is named by its NCT ID when the row has one, otherwise by its first column.
  const rowLabel = (row: Datum) => cellText(row, "nct_id") || (columns[0] ? cellText(row, columns[0].field) : "Row");
  const measure = columns.find((column) => column.type === "quantitative");
  const rowValue = (row: Datum) => {
    if (!measure) {
      return "";
    }
    const value = cellNumber(row, measure.field);
    return value === null ? "" : `${measure.title} ${formatWithUnit(value, measure)}`;
  };
  return <DataTable columns={columns} rows={spec.data} rowLabel={rowLabel} rowValue={rowValue} onSelect={onSelect} caption={spec.title} />;
}

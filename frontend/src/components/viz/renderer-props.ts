import type { DatumSelection, VizOf, VizType } from "@/lib/types";

/** The props of every renderer (PLAN 6.3): its own slice of the union, and the selection callback. */
export type RendererProps<K extends VizType> = {
  spec: VizOf<K>;
  onSelect: (selection: DatumSelection) => void;
};

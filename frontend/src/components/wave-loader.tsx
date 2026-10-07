"use client";

import { useEffect, useRef } from "react";

type WaveLoaderProps = {
  /** Edge length in pixels: 16 or 8 are the intended sizes. */
  size?: number;
  /** Dots per row and column. Defaults to 4 at 16 px and 3 below that. */
  matrix?: number;
  /** Seconds for the wave count to roll from -10 to 10 and back. */
  period?: number;
  className?: string;
  label?: string;
  /** Inside something that already says it is busy: hidden from assistive technology. */
  decorative?: boolean;
};

const WAVES_MIN = -10;
const WAVES_MAX = 10;

/**
 * A waiting indicator: a small matrix of dots whose radii follow a wave across the grid, in the manner
 * of the Durves dot patterns (matrix, dot radius, amplitude, waves). The wave count rolls from -10 to 10
 * and back, which makes the pattern shimmer. It takes the current text colour.
 */
export function WaveLoader({ size = 16, matrix, period = 9, className, label = "Working", decorative = false }: WaveLoaderProps) {
  const dots = matrix ?? (size >= 16 ? 4 : 3);
  const cell = size / dots;
  const maxRadius = cell * 0.46;
  const minRadius = cell * 0.12;
  const circles = useRef<(SVGCircleElement | null)[]>([]);

  useEffect(() => {
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
    const half = (dots - 1) / 2;
    const reach = Math.hypot(half, half) || 1;

    const draw = (waves: number) => {
      for (let row = 0; row < dots; row += 1) {
        for (let column = 0; column < dots; column += 1) {
          // Distance from the centre, 0 to 1: the wave travels outward along it.
          const distance = Math.hypot(column - half, row - half) / reach;
          const level = 0.5 + 0.5 * Math.sin(waves * Math.PI * distance);
          const circle = circles.current[row * dots + column];
          if (circle) {
            circle.setAttribute("r", (minRadius + (maxRadius - minRadius) * level).toFixed(3));
            circle.setAttribute("opacity", (0.35 + 0.65 * level).toFixed(3));
          }
        }
      }
    };

    if (still) {
      draw(1);
      return;
    }

    let frame = 0;
    const started = performance.now();
    const tick = (now: number) => {
      // A linear sweep of the wave count, so the shimmer moves at one steady speed: -10 at the start,
      // 10 half a period later, and back at the same rate.
      const turn = (((now - started) / 1000 / period) % 1) * 2;
      const progress = turn <= 1 ? turn : 2 - turn;
      draw(WAVES_MIN + (WAVES_MAX - WAVES_MIN) * progress);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [dots, maxRadius, minRadius, period]);

  return (
    <svg
      role={decorative ? undefined : "status"}
      aria-label={decorative ? undefined : label}
      aria-hidden={decorative || undefined}
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={className}
      fill="currentColor"
    >
      {Array.from({ length: dots * dots }, (_, index) => (
        <circle
          key={index}
          ref={(element) => {
            circles.current[index] = element;
          }}
          cx={(index % dots) * cell + cell / 2}
          cy={Math.floor(index / dots) * cell + cell / 2}
          r={minRadius}
        />
      ))}
    </svg>
  );
}

/**
 * Simulator geometry.
 *
 * The confidence mapping lives here rather than in the component because it is
 * a modelling decision, not a styling one, and it has to be identical
 * everywhere confidence is drawn — the soft edge on each impact panel and the
 * P10–P90 line under the net figure. Two views of the same uncertainty that
 * disagree would be worse than showing none.
 */

import type { SimulatorInput, SimulatorOutput } from '../data/types';

/**
 * How wide a spread has to be before we call it maximally uncertain.
 *
 * The fixture's P10–P90 spreads run 18 to 50 points. Normalising on 60 keeps
 * the whole observed range inside the scale with headroom, so no real result
 * pins to either end and loses its distinctness.
 */
const MAX_SPREAD = 60;

export interface ConfidenceShape {
  /** P90 − P10, in percentage points. */
  spread: number;
  /** 0–1. High means a tight forecast. */
  certainty: number;
  /** Fill opacity for the compact confidence bar. Narrow is saturated, wide is pale. */
  alpha: number;
  /**
   * How soft the flood's leading edge is, as a percentage of the panel's own
   * height. A confident forecast fills to a crisp line; an uncertain one
   * fades out over a wide band, so the uncertainty is the SHAPE of the edge,
   * not a separate readout you have to go find.
   */
  featherPct: number;
}

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

export function confidenceShape(out: SimulatorOutput): ConfidenceShape {
  const spread = Math.max(0, out.confidenceHigh - out.confidenceLow);
  const certainty = clamp(1 - spread / MAX_SPREAD, 0, 1);

  return {
    spread,
    certainty,
    alpha: 0.12 + certainty * 0.26,
    // 3% of the panel height at full certainty, out to ~28% at none.
    featherPct: 3 + (1 - certainty) * 25,
  };
}

/**
 * "Narrow" / "Moderate" / "Wide", from the band's actual width.
 *
 * WHAT THIS EXISTS TO PREVENT. `SimulatorOutput.confidencePercent` is a
 * confidence LEVEL — the backend computes it as `100 − spread` — and it is not
 * the band's width, not a ± tolerance, and not a percentage anything is plus
 * or minus. The Workforce card read it as one and printed "±63.9%" for a band
 * whose real width is 36.1 points, then called that band wide precisely
 * because confidence was HIGH. Both screens now go through this function, so
 * they cannot disagree about what "wide" means, and neither can reach for the
 * level when it wants the width.
 *
 * The width is always `confidenceHigh − confidenceLow`. There is no other
 * expression for it.
 */
export function bandVerdict(conf: ConfidenceShape): 'Narrow band' | 'Moderate band' | 'Wide band' {
  return conf.certainty > 0.55
    ? 'Narrow band'
    : conf.certainty > 0.35
      ? 'Moderate band'
      : 'Wide band';
}

export interface Lane {
  project: string;
  /** Positive slips later, negative pulls earlier. */
  deltaWeeks: number;
  role: 'source' | 'destination';
}

/** Always [source, destination] — left panel, then right panel. */
export function lanesFor(input: SimulatorInput, out: SimulatorOutput): Lane[] {
  return [
    { project: input.sourceProject, deltaWeeks: out.sourceDeltaWeeks, role: 'source' },
    { project: input.destProject, deltaWeeks: out.destDeltaWeeks, role: 'destination' },
  ];
}

/**
 * Headroom above the largest single-project delta the fixture produces.
 *
 * The three scenarios reach 6 weeks (Payments, pulled 6 weeks earlier by the
 * Platform move). 8 leaves room for a real backend to answer something
 * slightly larger without a panel already reading as completely full.
 */
const MAX_ABS_WEEKS = 8;

/** How full a panel's flood rises, 0–1. */
export function impactFraction(deltaWeeks: number): number {
  return clamp(Math.abs(deltaWeeks) / MAX_ABS_WEEKS, 0, 1);
}

/** Position of a panel's own delta on its mini timeline, 0–100. */
export function markerPct(deltaWeeks: number): number {
  return 50 + clamp(deltaWeeks / MAX_ABS_WEEKS, -1, 1) * 50;
}

export function sameInput(a: SimulatorInput, b: SimulatorInput): boolean {
  return (
    a.sourceProject === b.sourceProject &&
    a.destProject === b.destProject &&
    a.engineerCount === b.engineerCount
  );
}

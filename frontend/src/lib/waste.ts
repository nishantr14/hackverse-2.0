/**
 * Waste and risk aggregates.
 *
 * One deliberate modelling choice runs through this file: key-person exposure
 * is NOT added to the waste total. Rework, review latency and meeting cost are
 * money already spent that produced nothing — they are recoverable. Exposure is
 * the value of work at risk if one person becomes unavailable; it has not been
 * spent and may never be lost. Summing the two would produce a big number that
 * means nothing, so they are reported as two separate figures.
 */

import type { Tone } from '../components/GlassCard';
import type { WasteRow, WasteType } from '../data/types';

export const WASTE_ORDER: WasteType[] = [
  'meeting',
  'ci',
  'rework',
  'latency',
  'keyPerson',
];

/**
 * Colour is meaning, not identity, on this screen.
 *
 * Amber is waste — money spent that produced nothing. Coral is risk. Meeting
 * cost is waste too, but it is the smallest line by an order of magnitude, and
 * the spec's rule is to reuse grey before reaching for another hue rather than
 * to give every card its own colour.
 */
export const WASTE_TONE: Record<WasteType, Tone> = {
  rework: 'amber',
  latency: 'amber',
  meeting: 'neutral',
  ci: 'amber',
  keyPerson: 'coral',
};

/** The one-line explanation of what each category actually counts. */
export const WASTE_BASIS: Record<WasteType, string> = {
  rework:
    'Hours between a change request and the redo commit that answered it, priced at the redoer’s inferred band rate.',
  latency:
    'Wall-clock time finished work sat waiting for a first review. Reported as duration and never converted to rupees — nobody is billed to wait.',
  meeting:
    'Attendee count × scheduled duration × a blended band rate. Modelled from one assumption, not observed.',
  ci: 'Runner minutes burned on reruns and failed CI runs, at the published GitHub Actions per-minute price.',
  keyPerson:
    'Value of in-flight work in components where a single author owns most recent changes. At risk, not spent.',
};

export interface WasteCategory {
  type: WasteType;
  label: string;
  tone: Tone;
  amount: number;
  rows: WasteRow[];
  /** Share of the recoverable-waste total. Zero for exposure, which is excluded. */
  share: number;
  /** True for categories that count money already spent. */
  recoverable: boolean;
  /**
   * False when this category is deliberately never priced. `amount` is then
   * 0 and meaningless — read `hours` instead. A card must not render ₹0 for
   * these: zero rupees and "we refuse to invent a rupee figure" are
   * different claims, and only one of them is true here.
   */
  priced: boolean;
  /** Total duration behind the category. The readout when `priced` is false. */
  hours: number;
}

const LABEL: Record<WasteType, string> = {
  rework: 'Rework',
  latency: 'Review latency',
  meeting: 'Meeting cost',
  ci: 'CI rerun waste',
  keyPerson: 'Key-person exposure',
};

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

export function categorise(rows: WasteRow[]): WasteCategory[] {
  const recoverableTotal = rows
    .filter((r) => r.type !== 'keyPerson')
    .reduce((s, r) => s + r.amountRupees, 0);

  return WASTE_ORDER.map((type) => {
    const own = rows.filter((r) => r.type === type).sort((a, b) => b.amountRupees - a.amountRupees);
    const amount = own.reduce((s, r) => s + r.amountRupees, 0);
    const recoverable = type !== 'keyPerson';
    // A category is priced only if it has rows and every one of them is.
    const priced = own.length > 0 && own.every((r) => r.priced !== false);
    return {
      type,
      label: LABEL[type],
      tone: WASTE_TONE[type],
      amount,
      rows: own,
      share: recoverable && priced && recoverableTotal > 0 ? amount / recoverableTotal : 0,
      recoverable,
      priced,
      // Priced categories add up: rupees in two components are rupees.
      // Unpriced ones do NOT — each row is already a median wait, and
      // waiting runs in parallel, so summing across components would
      // invent centuries. Take the middle row instead.
      hours: priced
        ? own.reduce((s, r) => s + (r.hours ?? 0), 0)
        : median(own.map((r) => r.hours ?? 0)),
    };
  });
}

/** Money already spent that produced nothing. Excludes exposure by design. */
export function recoverableTotal(rows: WasteRow[]): number {
  return rows.filter((r) => r.type !== 'keyPerson').reduce((s, r) => s + r.amountRupees, 0);
}

export function exposureTotal(rows: WasteRow[]): number {
  return rows.filter((r) => r.type === 'keyPerson').reduce((s, r) => s + r.amountRupees, 0);
}

export interface ProjectWaste {
  project: string;
  recoverable: number;
  exposure: number;
}

export function byProjectWaste(rows: WasteRow[]): ProjectWaste[] {
  const map = new Map<string, ProjectWaste>();
  for (const r of rows) {
    let p = map.get(r.project);
    if (!p) {
      p = { project: r.project, recoverable: 0, exposure: 0 };
      map.set(r.project, p);
    }
    if (r.type === 'keyPerson') p.exposure += r.amountRupees;
    else p.recoverable += r.amountRupees;
  }
  return [...map.values()].sort(
    (a, b) => b.recoverable + b.exposure - (a.recoverable + a.exposure),
  );
}

/** Single biggest line anywhere in the waste log. */
export function largestLine(rows: WasteRow[]): WasteRow | null {
  return rows.reduce<WasteRow | null>(
    (best, r) => (best === null || r.amountRupees > best.amountRupees ? r : best),
    null,
  );
}

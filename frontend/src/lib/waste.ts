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

export const WASTE_ORDER: WasteType[] = ['rework', 'latency', 'meeting', 'keyPerson'];

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
  keyPerson: 'coral',
};

/** The one-line explanation of what each category actually counts. */
export const WASTE_BASIS: Record<WasteType, string> = {
  rework: 'Engineer-hours on code that was deleted or rewritten within 30 days, priced at role-band rates.',
  latency: 'Loaded salary cost of the time finished work sat waiting for a reviewer.',
  meeting: 'Scheduled duration × attendees × loaded rate, from calendar data.',
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
}

const LABEL: Record<WasteType, string> = {
  rework: 'Rework',
  latency: 'Review latency',
  meeting: 'Meeting cost',
  keyPerson: 'Key-person exposure',
};

export function categorise(rows: WasteRow[]): WasteCategory[] {
  const recoverableTotal = rows
    .filter((r) => r.type !== 'keyPerson')
    .reduce((s, r) => s + r.amountRupees, 0);

  return WASTE_ORDER.map((type) => {
    const own = rows.filter((r) => r.type === type).sort((a, b) => b.amountRupees - a.amountRupees);
    const amount = own.reduce((s, r) => s + r.amountRupees, 0);
    const recoverable = type !== 'keyPerson';
    return {
      type,
      label: LABEL[type],
      tone: WASTE_TONE[type],
      amount,
      rows: own,
      share: recoverable && recoverableTotal > 0 ? amount / recoverableTotal : 0,
      recoverable,
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

import type { SpendRow } from '../data/types';

/**
 * Project identity colours.
 *
 * Projects are nominal identities, and identity is what categorical colour is
 * for — this is the one place in the app where a hue means "which", not "how
 * much" or "how bad". Kept strictly separate from the semantic three:
 * coral/teal/amber still mean cost, gain and waste everywhere, and none of
 * them appears here.
 *
 * The four hues were not picked by eye. They were searched over OKLCH lightness
 * and chroma steps and validated with the dataviz palette validator against the
 * card surface, under **all-pairs** comparison (a treemap puts arbitrary tiles
 * side by side, so adjacent-only checking would hide a collapse):
 *
 *   worst all-pairs CVD ΔE 12.6 (deutan) — target is 8
 *   worst all-pairs normal-vision ΔE 16.1 — hard floor is 15
 *
 * Only four hue families survive: cyan and green-cyan collide with the semantic
 * teal, and everything warm collides with coral and amber. So the fifth and
 * smallest project takes neutral slate — the documented "fold the tail" move,
 * not an invented fifth hue.
 */

export interface ProjectColor {
  /** Base hue, for chips, dots and strokes. */
  base: string;
  /** `r g b` triplet, for building tints at arbitrary alpha. */
  rgb: string;
}

const SLOTS: ProjectColor[] = [
  { base: '#6593EB', rgb: '101 147 235' }, // blue
  { base: '#9737D5', rgb: '151 55 213' }, // violet
  { base: '#AC497A', rgb: '172 73 122' }, // plum
  { base: '#009B00', rgb: '0 155 0' }, // green
];

export const NEUTRAL_PROJECT: ProjectColor = { base: '#8B94A6', rgb: '139 148 166' };

export type ProjectPalette = Map<string, ProjectColor>;

/**
 * Assigns hues once, from the whole dataset, in descending total spend.
 *
 * Deliberately computed from the *full* log rather than from whatever is
 * currently on screen: colour follows the project, so filtering or regrouping
 * must never repaint the survivors.
 */
export function buildProjectPalette(rows: SpendRow[]): ProjectPalette {
  const totals = new Map<string, number>();
  for (const row of rows) {
    totals.set(row.project, (totals.get(row.project) ?? 0) + row.cost);
  }

  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([project]) => project);

  const palette: ProjectPalette = new Map();
  ranked.forEach((project, i) => {
    palette.set(project, SLOTS[i] ?? NEUTRAL_PROJECT);
  });
  return palette;
}

export function colorFor(palette: ProjectPalette, project: string): ProjectColor {
  return palette.get(project) ?? NEUTRAL_PROJECT;
}

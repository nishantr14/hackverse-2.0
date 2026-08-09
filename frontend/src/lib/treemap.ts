/**
 * Squarified treemap layout (Bruls, Huizing & van Wijk, 2000).
 *
 * Written by hand rather than pulled from a chart library because the tile
 * chrome — 2px surface gaps, group headers, the waste marker — needs exact
 * control, and because area is the only thing encoding magnitude here.
 */

export interface TreemapInput<T> {
  value: number;
  datum: T;
}

export interface TreemapRect<T> {
  x: number;
  y: number;
  w: number;
  h: number;
  value: number;
  datum: T;
}

function worstRatio(areas: number[], sum: number, side: number): number {
  let max = -Infinity;
  let min = Infinity;
  for (const a of areas) {
    if (a > max) max = a;
    if (a < min) min = a;
  }
  const s2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
}

export function squarify<T>(
  input: TreemapInput<T>[],
  x0: number,
  y0: number,
  w0: number,
  h0: number,
): TreemapRect<T>[] {
  const items = input.filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
  const total = items.reduce((s, i) => s + i.value, 0);
  if (total <= 0 || w0 <= 0 || h0 <= 0) return [];

  const scale = (w0 * h0) / total;
  const areas = items.map((i) => i.value * scale);

  const out: TreemapRect<T>[] = [];
  let x = x0;
  let y = y0;
  let w = w0;
  let h = h0;
  let cursor = 0;

  while (cursor < areas.length) {
    const side = Math.min(w, h);
    const row: number[] = [];
    let rowSum = 0;
    let best = Infinity;

    while (cursor + row.length < areas.length) {
      const next = areas[cursor + row.length];
      const candidateSum = rowSum + next;
      const ratio = worstRatio([...row, next], candidateSum, side);
      if (row.length === 0 || ratio <= best) {
        row.push(next);
        rowSum = candidateSum;
        best = ratio;
      } else {
        break;
      }
    }

    if (w >= h) {
      // Lay the row out as a vertical strip down the left of the remaining space.
      const stripW = rowSum / h;
      let cy = y;
      row.forEach((area, k) => {
        const tileH = area / stripW;
        const item = items[cursor + k];
        out.push({ x, y: cy, w: stripW, h: tileH, value: item.value, datum: item.datum });
        cy += tileH;
      });
      x += stripW;
      w -= stripW;
    } else {
      // Lay the row out as a horizontal band across the top.
      const stripH = rowSum / w;
      let cx = x;
      row.forEach((area, k) => {
        const tileW = area / stripH;
        const item = items[cursor + k];
        out.push({ x: cx, y, w: tileW, h: stripH, value: item.value, datum: item.datum });
        cx += tileW;
      });
      y += stripH;
      h -= stripH;
    }

    cursor += row.length;
  }

  return out;
}

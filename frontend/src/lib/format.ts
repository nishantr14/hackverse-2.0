/**
 * Formatting rules for the whole app.
 *
 * Money is always ₹ in lakh/crore notation above ₹1L — a director reads
 * "₹33.1L", never "3310000". Below a lakh we fall back to Indian digit
 * grouping rather than inventing a smaller unit.
 */

const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

const LAKH = 100_000;
const CRORE = 10_000_000;

/** Trims a trailing ".0" so we render ₹12L rather than ₹12.0L. */
function trim(n: number, places: number): string {
  return n.toFixed(places).replace(/\.0+$/, '');
}

/** ₹8.2L · ₹1.23Cr · ₹62,000 */
export function formatMoney(rupees: number): string {
  const abs = Math.abs(rupees);
  const sign = rupees < 0 ? '−' : '';

  if (abs >= CRORE) return `${sign}₹${trim(abs / CRORE, 2)}Cr`;
  if (abs >= LAKH) return `${sign}₹${trim(abs / LAKH, 1)}L`;
  return `${sign}₹${inr.format(abs)}`;
}

/**
 * Same as formatMoney but always carries an explicit + or −.
 * Used wherever a number is colour-coded, so the sign — not the colour —
 * is what actually carries the meaning.
 */
export function formatMoneyDelta(rupees: number): string {
  if (rupees === 0) return '₹0';
  const body = formatMoney(Math.abs(rupees));
  return `${rupees > 0 ? '+' : '−'}${body}`;
}

/**
 * A money formatter locked to one unit, chosen from the target value.
 *
 * Counting a figure up from zero with `formatMoney` would flip units mid-count
 * (₹0 → ₹90,000 → ₹9.3L), which reads as a glitch. This picks the unit once
 * from where the number is going and holds it for every frame.
 */
export function moneyScaleFormatter(target: number): (n: number) => string {
  const abs = Math.abs(target);
  if (abs >= CRORE) return (n) => `₹${(n / CRORE).toFixed(2)}Cr`;
  if (abs >= LAKH) return (n) => `₹${(n / LAKH).toFixed(1)}L`;
  return (n) => `₹${inr.format(Math.round(n))}`;
}

/** Exact rupees, for tooltips and the table view where precision is the point. */
export function formatRupeesExact(rupees: number): string {
  return `₹${inr.format(rupees)}`;
}

/** 37.5% */
export function formatPercent(fraction: number, places = 1): string {
  return `${trim(fraction * 100, places)}%`;
}

/** 64 h · 1,204 h */
export function formatHours(hours: number): string {
  return `${inr.format(hours)} h`;
}

/** "5 weeks later" / "3 weeks earlier" / "no change" */
export function formatWeekDelta(weeks: number): string {
  if (weeks === 0) return 'No change to timeline';
  const n = Math.abs(weeks);
  const unit = n === 1 ? 'week' : 'weeks';
  return weeks > 0 ? `${n} ${unit} later` : `${n} ${unit} earlier`;
}

/** Human label for a waste category. */
export const wasteLabel: Record<string, string> = {
  rework: 'Rework',
  latency: 'Review latency',
  meeting: 'Meeting cost',
  keyPerson: 'Key-person exposure',
};

import { motion } from 'framer-motion';
import { AnimatedNumber } from './AnimatedNumber';
import { toneStyle } from './GlassCard';
import { Tip } from './Tip';
import { formatMoney, moneyScaleFormatter } from '../lib/format';
import { blurIn, snap } from '../lib/motion';
import { WASTE_BASIS, type WasteCategory } from '../lib/waste';

/**
 * The four waste cards.
 *
 * Tint and glow scale with the size of the figure, so the ₹45L card and the
 * ₹2.4L card cannot look alike — magnitude is carried by the surface, not only
 * by the digits. Selecting a card filters the ledger beneath it.
 *
 * Exposure carries an explicit "at risk, not spent" chip rather than a share
 * percentage: it is not part of the recoverable total and must not read as
 * though it were.
 */

interface WasteCardsProps {
  categories: WasteCategory[];
  selected: string | null;
  onSelect: (type: string | null) => void;
}

/** A median over fewer items than this is not a median, it is one sample. */
const MIN_SAMPLE = 5;

/**
 * Rows arrive sorted by rupees, so `rows[0]` is the biggest line — but an
 * unpriced category's rows are all zero rupees, and its interesting row is
 * the slowest one, not whichever happened to sort first.
 *
 * Components with almost no PRs are skipped: a component holding a single
 * ancient ticket reported a "median" wait of 3,100 days and would have been
 * the headline, which says something about that one row and nothing about
 * how this team reviews.
 */
function slowest(c: WasteCategory) {
  const eligible = c.rows.filter((r) => r.nItems >= MIN_SAMPLE);
  const pool = eligible.length > 0 ? eligible : c.rows;
  return pool.reduce((worst, r) => (r.hours > worst.hours ? r : worst), pool[0]);
}

export function WasteCards({ categories, selected, onSelect }: WasteCardsProps) {
  const max = Math.max(...categories.map((c) => c.amount), 1);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {categories.map((c) => {
        const isSelected = selected === c.type;
        const dim = selected !== null && !isSelected;
        const intensity = c.amount / max;

        return (
          <motion.button
            key={c.type}
            type="button"
            variants={blurIn}
            aria-pressed={isSelected}
            onClick={() => onSelect(isSelected ? null : c.type)}
            animate={{ opacity: dim ? 0.45 : 1 }}
            whileHover={{ y: -3 }}
            transition={snap}
            className="rounded-xl border p-6 text-left"
            style={{
              ...toneStyle(c.tone, intensity),
              outline: isSelected ? '1px solid var(--ui-active-border)' : undefined,
              outlineOffset: 2,
            }}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                {c.label}
              </p>
              {(!c.recoverable || !c.priced) && (
                <span
                  className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium"
                  style={{
                    background: 'rgb(255 255 255 / 0.07)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  {!c.recoverable ? (c.rows.length ? 'at risk' : 'off-API') : 'not priced'}
                </span>
              )}
            </div>

            {/* An unpriced category shows its DURATION. Rendering ₹0 would
                read as "this cost nothing", which is a different and false
                claim from "we will not put a rupee figure on waiting". */}
            <p className="mt-3.5">
              <Tip label={c.label} formula={WASTE_BASIS[c.type]}>
                <span className="tnum block text-[30px] leading-none font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
                  {/* Key-person exposure has no rows because it is never
                      served over HTTP — it reads a per-actor view that is
                      deliberately not granted to the API role. Rendering ₹0
                      would claim we measured it and found nothing. */}
                  {!c.recoverable && c.rows.length === 0 ? (
                    <span className="text-[19px]">Not served</span>
                  ) : c.priced ? (
                    <AnimatedNumber value={c.amount} format={moneyScaleFormatter(c.amount)} />
                  ) : (
                    <AnimatedNumber
                      value={c.hours / 24}
                      format={(v) => `${v.toFixed(1)} days`}
                    />
                  )}
                </span>
              </Tip>
            </p>

            <p className="mt-3 text-[12px] leading-relaxed text-[var(--text-secondary)]">
              {!c.recoverable
                ? c.rows.length === 0
                  ? 'Per-actor by construction, so it is computed offline and never exposed through the API'
                  : `Not spent — value exposed across ${c.rows.length} component${
                      c.rows.length === 1 ? '' : 's'
                    }`
                : c.priced
                  ? `${Math.round(c.share * 100)}% of recoverable waste · ${c.rows.length} line${
                      c.rows.length === 1 ? '' : 's'
                    }`
                  : `Median component wait, across ${c.rows.length} component${
                      c.rows.length === 1 ? '' : 's'
                    } — waiting is not billed`}
            </p>

            <p className="mt-2 text-[11.5px] text-[var(--text-secondary)]">
              {c.rows[0] ? (
                <>
                  {c.priced ? 'Largest: ' : 'Slowest: '}
                  {c.priced
                    ? formatMoney(c.rows[0].amountRupees)
                    : `${(slowest(c).hours / 24).toFixed(1)} days`}
                  {(c.priced ? c.rows[0] : slowest(c)).component
                    ? ` in ${(c.priced ? c.rows[0] : slowest(c)).component}`
                    : ''}
                </>
              ) : (
                <>Run `python -m app.waste.key_person` to see it</>
              )}
            </p>
          </motion.button>
        );
      })}
    </div>
  );
}

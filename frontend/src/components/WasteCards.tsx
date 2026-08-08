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
              {!c.recoverable && (
                <span
                  className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium"
                  style={{
                    background: 'rgb(255 255 255 / 0.07)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  at risk
                </span>
              )}
            </div>

            <p className="mt-3.5">
              <Tip label={c.label} formula={WASTE_BASIS[c.type]}>
                <span className="tnum block text-[30px] leading-none font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
                  <AnimatedNumber value={c.amount} format={moneyScaleFormatter(c.amount)} />
                </span>
              </Tip>
            </p>

            <p className="mt-3 text-[12px] leading-relaxed text-[var(--text-secondary)]">
              {c.recoverable
                ? `${Math.round(c.share * 100)}% of recoverable waste · ${c.rows.length} line${
                    c.rows.length === 1 ? '' : 's'
                  }`
                : `Not spent — value exposed across ${c.rows.length} component${
                    c.rows.length === 1 ? '' : 's'
                  }`}
            </p>

            <p className="mt-2 text-[11.5px] text-[var(--text-secondary)]">
              Largest: {c.rows[0] ? formatMoney(c.rows[0].amountRupees) : '—'}
              {c.rows[0]?.component ? ` in ${c.rows[0].component}` : ''}
            </p>
          </motion.button>
        );
      })}
    </div>
  );
}

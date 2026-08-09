import { motion } from 'framer-motion';
import { formatPercent } from '../lib/format';
import { EASE_GLASS, snap } from '../lib/motion';
import { variantTone, type VariantStat } from '../lib/process';

/**
 * Work share against cost share, one variant per row.
 *
 * Two bars from a shared left edge is the cheapest way to make a
 * disproportion visible: when the cost bar overshoots the work bar, that path
 * is charging more than its weight, and you can see it without reading either
 * number. The multiple is printed as well so the claim survives being
 * screenshotted in greyscale.
 */

interface VariantBarsProps {
  stats: VariantStat[];
  selected: string | null;
  onSelect: (variant: string | null) => void;
  /**
   * 'stack' (default) renders its own vertical list — drop it in anywhere.
   * 'items' renders bare rows with no wrapper, so a parent grid can lay them
   * out 3-up instead of stacked.
   */
  layout?: 'stack' | 'items';
}

export function VariantBars({ stats, selected, onSelect, layout = 'stack' }: VariantBarsProps) {
  const rows = stats.map((s, i) => {
        const tone = variantTone(s.variant);
        const isSelected = selected === s.variant;
        const dim = selected !== null && !isSelected;
        const overpriced = s.costMultiple > 1;

        return (
          <motion.button
            key={s.variant}
            type="button"
            aria-pressed={isSelected}
            onClick={() => onSelect(isSelected ? null : s.variant)}
            animate={{ opacity: dim ? 0.4 : 1 }}
            transition={snap}
            className="rounded-lg px-3 py-2.5 text-left transition-colors"
            style={{
              background: isSelected ? 'var(--ui-active)' : 'transparent',
              border: `1px solid ${isSelected ? 'var(--ui-active-border)' : 'transparent'}`,
            }}
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="flex items-center gap-2.5">
                <span
                  aria-hidden
                  className="block h-2.5 w-2.5 rounded-full"
                  style={{ background: tone.css }}
                />
                <span className="text-[13.5px] font-semibold text-[var(--text-primary)]">
                  {s.label}
                </span>
              </span>
              <span
                className="tnum text-[13px] font-semibold"
                style={{ color: overpriced ? tone.css : 'var(--text-secondary)' }}
              >
                {/* The arrow, not the colour, is what says "more than its share". */}
                {overpriced ? '↑' : '↓'} {s.costMultiple.toFixed(1)}×
              </span>
            </div>

            <div className="mt-3 flex flex-col gap-1.5">
              <Bar
                term="Work items"
                share={s.shareOfWorkItems}
                color="rgb(94 107 128)"
                delay={0.1 * i}
              />
              <Bar term="Cost" share={s.shareOfCost} color={tone.css} delay={0.1 * i + 0.08} />
            </div>
          </motion.button>
        );
      });

  if (layout === 'items') return <>{rows}</>;
  return <div className="flex flex-col gap-5">{rows}</div>;
}

function Bar({
  term,
  share,
  color,
  delay,
}: {
  term: string;
  share: number;
  color: string;
  delay: number;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-[68px] shrink-0 text-[11.5px] text-[var(--text-secondary)]">{term}</span>
      <span
        className="relative h-2.5 flex-1 overflow-hidden rounded-full"
        style={{ background: 'rgb(255 255 255 / 0.05)' }}
      >
        <motion.span
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ background: color, transformOrigin: 'left' }}
          initial={{ width: 0 }}
          whileInView={{ width: `${share * 100}%` }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.8, ease: EASE_GLASS, delay }}
        />
      </span>
      <span className="tnum w-[46px] shrink-0 text-right text-[12px] text-[var(--text-primary)]">
        {formatPercent(share, 0)}
      </span>
    </div>
  );
}

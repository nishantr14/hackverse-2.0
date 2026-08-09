import type { ProjectedImpact as Impact } from '../data/types';
import { formatMoney } from '../lib/format';
import { GlassCard } from './GlassCard';

/**
 * What the simulator projects if this recommendation were acted on.
 *
 * Three deltas, never three absolute figures — an absolute number here would
 * read as a measurement, and none of this has happened yet. The arrow and the
 * sign both carry the direction so the meaning does not live in the colour
 * alone, and the word "projected" appears on the panel rather than only in a
 * tooltip.
 *
 * Sign convention matches the type: negative is an improvement. Nothing here
 * decides per metric which way is good — it reads the sign.
 */

function Metric({ label, value, better }: { label: string; value: string; better: boolean }) {
  const color = better ? 'var(--teal)' : 'var(--coral)';
  return (
    <div
      className="rounded-xl border px-5 py-4"
      style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.7)' }}
    >
      <p className="text-[11px] tracking-[0.06em] text-[var(--text-secondary)] uppercase">
        {label}
      </p>
      <p className="tnum mt-3 flex items-baseline gap-1.5 text-[24px] leading-none font-semibold" style={{ color }}>
        <span aria-hidden>{better ? '↓' : '↑'}</span>
        {value}
      </p>
    </div>
  );
}

export function ProjectedImpactPanel({ impact }: { impact: Impact }) {
  const pct = (n: number) => `${Math.abs(n)}%`;

  return (
    <GlassCard className="p-5" animate={false}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">Projected impact</h2>
        <p className="text-[11.5px] text-[var(--text-muted)]">
          Projected by the simulator, not observed. Nothing here has happened yet.
        </p>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Metric
          label="Cycle time"
          value={pct(impact.cycleTimePct)}
          better={impact.cycleTimePct < 0}
        />
        <Metric
          label="Review latency"
          value={pct(impact.reviewLatencyPct)}
          better={impact.reviewLatencyPct < 0}
        />
        <Metric
          label="Estimated cost"
          value={formatMoney(Math.abs(impact.costRupees))}
          better={impact.costRupees < 0}
        />
      </div>
    </GlassCard>
  );
}

import { motion } from 'framer-motion';
import type { CSSProperties, ReactNode } from 'react';
import { EASE_GLASS, blurIn } from '../lib/motion';
import { Tip } from './Tip';

/**
 * One band, not four boxes.
 *
 * A row of equal cards reads as a slide. The lead figure owns the band and the
 * supporting figures sit beside it behind hairlines, which is how a product
 * shows what matters most.
 */

export interface Metric {
  label: string;
  value: ReactNode;
  detail: ReactNode;
  formula: ReactNode;
}

interface MetricStripProps {
  hero: Metric;
  metrics: Metric[];
}

export function MetricStrip({ hero, metrics }: MetricStripProps) {
  return (
    <motion.div
      variants={blurIn}
      className="relative overflow-hidden rounded-xl border"
      style={{
        borderColor: 'var(--border)',
        background: 'var(--bg-card)',
        boxShadow: 'inset 0 1px 0 rgb(255 255 255 / 0.05)',
      }}
    >
      <div className="flex flex-col lg:flex-row">
        <div className="relative shrink-0 px-7 py-6 lg:w-[24rem]">
          <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
            {hero.label}
          </p>

          <div className="mt-3.5">
            <Tip label={hero.label} formula={hero.formula}>
              <span className="tnum block text-[44px] leading-none font-semibold tracking-[-0.025em] text-[var(--text-primary)]">
                {hero.value}
              </span>
            </Tip>
          </div>

          {/* draws in as the figure settles — the band is being measured, not shown */}
          <motion.span
            aria-hidden
            className="mt-4 block h-px origin-left"
            style={{
              background:
                'linear-gradient(90deg, rgb(255 255 255 / 0.45), rgb(255 255 255 / 0.04) 70%, transparent)',
            }}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 1.1, ease: EASE_GLASS, delay: 0.15 }}
          />

          <p className="mt-3 text-[12px] leading-relaxed text-[var(--text-secondary)]">
            {hero.detail}
          </p>
        </div>

        <div
          className="grid flex-1 grid-cols-1 border-t sm:grid-cols-[repeat(var(--metric-cols),minmax(0,1fr))] lg:border-t-0 lg:border-l"
          style={
            {
              borderColor: 'var(--border)',
              // Columns follow the number of metrics — a fixed 3 left a dead
              // column whenever a screen supplied two.
              '--metric-cols': metrics.length,
            } as CSSProperties
          }
        >
          {metrics.map((m, i) => (
            <motion.div
              key={m.label}
              className="group relative px-6 py-6"
              style={{
                borderLeftWidth: i === 0 ? 0 : 1,
                borderLeftColor: 'var(--border)',
                borderLeftStyle: 'solid',
              }}
              whileHover={{ backgroundColor: 'rgba(255,255,255,0.035)' }}
              transition={{ duration: 0.25, ease: EASE_GLASS }}
            >
              <span
                aria-hidden
                className="absolute inset-x-0 top-0 h-px scale-x-0 bg-[var(--ui)] opacity-70 transition-transform duration-300 group-hover:scale-x-100"
              />
              <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                {m.label}
              </p>
              <p className="mt-3.5">
                <Tip label={m.label} formula={m.formula}>
                  <span className="tnum block text-[26px] leading-none font-semibold tracking-[-0.015em] text-[var(--text-primary)]">
                    {m.value}
                  </span>
                </Tip>
              </p>
              <p className="mt-2.5 text-[12px] leading-relaxed text-[var(--text-secondary)]">
                {m.detail}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </motion.div>
  );
}

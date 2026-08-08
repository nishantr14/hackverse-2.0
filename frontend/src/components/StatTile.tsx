import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { blurIn } from '../lib/motion';
import { Tip } from './Tip';

interface StatTileProps {
  label: string;
  value: string;
  /** Supporting line under the value — the denominator, the share, the count. */
  detail?: ReactNode;
  /** Derivation shown on hover/focus. Every computed number carries one. */
  formula?: ReactNode;
  /** The lead figure on a screen renders larger. */
  hero?: boolean;
}

export function StatTile({ label, value, detail, formula, hero = false }: StatTileProps) {
  const figure = (
    <span
      className={
        hero
          ? 'block text-[38px] leading-none font-semibold tracking-[-0.02em] text-[var(--text-primary)]'
          : 'block text-[24px] leading-none font-semibold tracking-[-0.01em] text-[var(--text-primary)]'
      }
    >
      {value}
    </span>
  );

  return (
    <motion.div
      variants={blurIn}
      className="rounded-xl border px-5 py-4"
      style={{
        borderColor: 'var(--border)',
        background: 'rgb(19 23 34 / 0.7)',
      }}
    >
      <p className="text-[11px] tracking-[0.06em] text-[var(--text-secondary)] uppercase">{label}</p>
      <p className="mt-3">
        {formula ? (
          <Tip label={label} formula={formula}>
            {figure}
          </Tip>
        ) : (
          figure
        )}
      </p>
      {detail && (
        <p className="mt-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">{detail}</p>
      )}
    </motion.div>
  );
}

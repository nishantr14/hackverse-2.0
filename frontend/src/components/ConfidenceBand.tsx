import { motion } from 'framer-motion';
import { EASE_GLASS } from '../lib/motion';
import type { ConfidenceShape } from '../lib/simulator';

/**
 * Confidence, drawn before it is stated.
 *
 * The band is the primary read and the percentage is the footnote, which is the
 * inversion the spec asks for: a wide pale band says "we are guessing" faster
 * than "52%" does, and it cannot be mistaken for a precise claim the way a
 * two-digit number can. The percentage is still printed, because a band alone
 * cannot be quoted in a meeting.
 */

interface ConfidenceBandProps {
  low: number;
  high: number;
  percent?: number;
  conf: ConfidenceShape;
  /** Tint of the figure this band belongs to. */
  rgb: string;
  revealed: boolean;
}

export function ConfidenceBand({ low, high, percent, conf, rgb, revealed }: ConfidenceBandProps) {
  const verdict =
    conf.certainty > 0.55 ? 'Narrow band' : conf.certainty > 0.35 ? 'Moderate band' : 'Wide band';

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
          Confidence
        </span>
        <span className="text-[12px] text-[var(--text-secondary)]">
          {/* Words as well as width, so the encoding survives greyscale. */}
          {verdict} · P10–P90 {low}–{high}%
          {percent !== undefined && <span className="tnum"> · {percent}%</span>}
        </span>
      </div>

      <div
        className="relative mt-2.5 h-9 overflow-hidden rounded-lg"
        style={{ background: 'rgb(255 255 255 / 0.04)', border: '1px solid var(--border)' }}
      >
        {[25, 50, 75].map((t) => (
          <span
            key={t}
            aria-hidden
            className="absolute inset-y-0 w-px"
            style={{ left: `${t}%`, background: 'rgb(255 255 255 / 0.05)' }}
          />
        ))}

        <motion.div
          aria-hidden
          className="absolute inset-y-0 rounded-md"
          style={{
            left: `${low}%`,
            width: `${high - low}%`,
            background: `linear-gradient(90deg, rgb(${rgb} / ${conf.alpha * 0.4}), rgb(${rgb} / ${conf.alpha}) 50%, rgb(${rgb} / ${conf.alpha * 0.4}))`,
            border: `1px solid rgb(${rgb} / ${conf.alpha + 0.14})`,
          }}
          initial={{ opacity: 0, scaleX: 0.3 }}
          animate={revealed ? { opacity: 1, scaleX: 1 } : { opacity: 0, scaleX: 0.3 }}
          transition={{ duration: 0.7, ease: EASE_GLASS, delay: 0.5 }}
        />

        {percent !== undefined && (
          <motion.span
            aria-hidden
            className="absolute inset-y-1 w-[2px] rounded-full"
            style={{ left: `${percent}%`, background: `rgb(${rgb})` }}
            initial={{ opacity: 0 }}
            animate={{ opacity: revealed ? 0.9 : 0 }}
            transition={{ duration: 0.4, ease: EASE_GLASS, delay: 0.8 }}
          />
        )}
      </div>

      <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
        The band is the forecast. A wider band means the event log supports a wider range of
        outcomes for this move — it is not a margin of error on a single answer.
      </p>
    </div>
  );
}

import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { EASE_GLASS } from '../lib/motion';

/**
 * The calculating state.
 *
 * Held deliberately, not skipped. A forecast that appears the instant you click
 * reads as a lookup table; one that visibly does work reads as a model. The
 * steps below are the actual stages of the forecast, in order, so the pause is
 * informative rather than theatrical.
 */

const STEPS = [
  'Reading the event log for both projects',
  'Pricing in-flight work at role-band rates',
  'Weighting evidenced experience per component',
  'Sampling delivery outcomes for the P10–P90 band',
];

export function Calculating({ stepMs = 240 }: { stepMs?: number }) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), stepMs);
    return () => clearInterval(id);
  }, [stepMs]);

  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        {[0, 1].map((lane) => (
          <div
            key={lane}
            className="relative h-14 overflow-hidden rounded-lg"
            style={{ background: 'rgb(255 255 255 / 0.035)', border: '1px solid var(--border)' }}
          >
            <motion.span
              aria-hidden
              className="absolute inset-y-0 w-1/3"
              style={{
                background:
                  'linear-gradient(90deg, transparent, rgb(232 236 244 / 0.10), transparent)',
              }}
              initial={{ left: '-33%' }}
              animate={{ left: '100%' }}
              transition={{
                duration: 1.1,
                ease: 'linear',
                repeat: Infinity,
                delay: lane * 0.22,
              }}
            />
          </div>
        ))}
      </div>

      <ul className="flex flex-col gap-2">
        {STEPS.map((s, i) => (
          <motion.li
            key={s}
            className="flex items-center gap-3 text-[12.5px]"
            animate={{ opacity: i <= step ? 1 : 0.3 }}
            transition={{ duration: 0.3, ease: EASE_GLASS }}
          >
            <span
              aria-hidden
              className="block h-1.5 w-1.5 shrink-0 rounded-full"
              style={{
                background: i <= step ? 'var(--ui)' : 'var(--border-strong)',
              }}
            />
            <span style={{ color: i <= step ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
              {s}
            </span>
          </motion.li>
        ))}
      </ul>
    </div>
  );
}

import { AnimatePresence, motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { EASE_GLASS } from '../lib/motion';

/**
 * The screen's one sentence.
 *
 * A director should be able to read a single line and know what this screen
 * says. It rewrites itself as you point at the data, so the explanation and the
 * visual are never out of step — and it means the page leads with a claim
 * rather than with a grid of small numbers.
 *
 * Lives inside the sticky header, so `compact` shrinks it to one line while you
 * are scrolled into the map.
 */

interface HeadlineProps {
  /** Changing this swaps the sentence with a transition. */
  id: string;
  children: ReactNode;
  sub?: ReactNode;
  compact?: boolean;
}

export function Headline({ id, children, sub, compact = false }: HeadlineProps) {
  return (
    <div style={{ minHeight: compact ? 0 : 72 }}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div
          key={id}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -12 }}
          transition={{ duration: 0.35, ease: EASE_GLASS }}
        >
          <p
            className="max-w-5xl font-semibold text-[var(--text-primary)]"
            style={{
              fontSize: compact ? 19 : 24,
              lineHeight: 1.3,
              letterSpacing: '-0.018em',
              display: compact ? '-webkit-box' : 'block',
              WebkitLineClamp: compact ? 1 : undefined,
              WebkitBoxOrient: compact ? 'vertical' : undefined,
              overflow: compact ? 'hidden' : undefined,
            }}
          >
            {children}
          </p>
          {sub && !compact && (
            <p className="mt-2 max-w-4xl text-[13px] leading-relaxed text-[var(--text-secondary)]">
              {sub}
            </p>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

/** A number inside the headline. Mono, like every other figure in the app. */
export function Figure({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className="tnum font-semibold" style={color ? { color } : undefined}>
      {children}
    </span>
  );
}

/**
 * The name of a project or component inside the headline, tinted to match its
 * territory on the map.
 *
 * Deliberately NOT `Figure`: the mono face is for figures, and running a word
 * like "notification-engine" through it makes the sentence read like a log line.
 */
export function Name({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className="font-semibold" style={color ? { color } : undefined}>
      {children}
    </span>
  );
}

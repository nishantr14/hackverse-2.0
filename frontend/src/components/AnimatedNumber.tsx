import { animate, useReducedMotion } from 'framer-motion';
import { useEffect, useState } from 'react';

/**
 * A figure that counts up to its value on mount, and re-counts whenever the
 * value changes.
 *
 * This is not decoration — it is the difference between a number that was
 * typed onto a slide and a number that was just computed.
 *
 * Deliberately not gated on an IntersectionObserver: a headline figure stuck
 * at ₹0 because an observer never fired is the worst failure this screen can
 * have, and it would only show up in front of a judge. It respects
 * prefers-reduced-motion by rendering the final value immediately.
 */

interface AnimatedNumberProps {
  value: number;
  /** Must hold a single unit across the whole count — see moneyScaleFormatter. */
  format: (n: number) => string;
  duration?: number;
  className?: string;
}

export function AnimatedNumber({ value, format, duration = 1.1, className }: AnimatedNumberProps) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(value);

  useEffect(() => {
    if (reduce) {
      setShown(value);
      return;
    }

    const controls = animate(0, value, {
      duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: setShown,
    });
    return () => controls.stop();
  }, [value, reduce, duration]);

  return (
    <span className={className}>
      {/* The settled value is what assistive tech reads; the count is visual. */}
      <span aria-hidden>{format(shown)}</span>
      <span className="sr-only">{format(value)}</span>
    </span>
  );
}

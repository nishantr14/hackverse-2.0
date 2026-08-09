import { AnimatePresence, motion } from 'framer-motion';
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { snap } from '../lib/motion';

/**
 * Formula tooltip.
 *
 * Every computed figure in the app can explain itself. The tooltip never holds
 * a value that is unavailable elsewhere — it holds the derivation, which is a
 * different thing. Opens on hover AND on keyboard focus.
 *
 * Rendered in a portal on the body and positioned `fixed` from the trigger's
 * own rect. An in-flow absolute panel is clipped the moment any ancestor sets
 * `overflow: hidden` — which the metric band does, for its rounded corners —
 * and that failure stays invisible until a tooltip happens to open near an
 * edge. A portal makes it structurally impossible rather than a rule to
 * remember on every new card.
 */

const WIDTH = 320;
const GAP = 10;
/** With less headroom than this above the trigger, the panel flips below it. */
const ROOM_ABOVE = 200;

interface TipProps {
  /** Short title, e.g. "Blended cost per hour". */
  label: string;
  /** The derivation, in words a director can follow. */
  formula: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * When placed above the trigger we anchor with `bottom`, not `top` plus a
 * `translateY(-100%)`: framer-motion owns the `transform` property while it is
 * animating `y`, so an inline transform is silently overwritten and the panel
 * lands on top of the very number it is explaining.
 */
interface Placement {
  left: number;
  /** Set when placing below the trigger. */
  top?: number;
  /** Set when placing above the trigger — distance from the viewport bottom. */
  bottom?: number;
  above: boolean;
}

export function Tip({ label, formula, children, className = '' }: TipProps) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Placement | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const id = useId();

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const left = Math.min(
      Math.max(r.left + r.width / 2 - WIDTH / 2, 12),
      Math.max(window.innerWidth - WIDTH - 12, 12),
    );
    const above = r.top > ROOM_ABOVE;
    setPlace(
      above
        ? { left, bottom: window.innerHeight - r.top + GAP, above }
        : { left, top: r.bottom + GAP, above },
    );
  }, []);

  useLayoutEffect(() => {
    if (open) measure();
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const reposition = () => measure();
    // `true` so it also catches scrolling of any inner scroll container.
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open, measure]);

  return (
    <>
      <span
        ref={triggerRef}
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
        className={`inline-flex cursor-help underline decoration-dotted decoration-1 underline-offset-4 ${className}`}
        style={{ textDecorationColor: 'var(--border-strong)' }}
      >
        {children}
      </span>

      {createPortal(
        <AnimatePresence>
          {open && place && (
            <motion.div
              id={id}
              role="tooltip"
              initial={{ opacity: 0, y: place.above ? 6 : -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: place.above ? 6 : -6 }}
              transition={snap}
              className="pointer-events-none fixed z-[100] rounded-lg border p-3.5 text-left"
              style={{
                left: place.left,
                top: place.top,
                bottom: place.bottom,
                width: WIDTH,
                background: 'var(--bg-card)',
                borderColor: 'var(--border-strong)',
                boxShadow: '0 24px 60px -24px rgb(0 0 0 / 0.95)',
              }}
            >
              <p className="text-[11.5px] font-semibold tracking-wide text-[var(--text-primary)]">
                {label}
              </p>
              <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--text-secondary)]">
                {formula}
              </p>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}

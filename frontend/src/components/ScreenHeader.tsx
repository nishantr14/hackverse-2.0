import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

/**
 * Sticky screen header.
 *
 * The `headline` slot lives here rather than in the page body on purpose: it is
 * the sentence that rewrites itself as you point at the data, so it has to stay
 * on screen while you are pointing. Parked in the scrolling body it scrolled
 * away exactly when it started saying something.
 *
 * Because it is pinned it has to stay small — every pixel here is a pixel the
 * map does not get. Once you scroll, the title and lede collapse and the
 * sentence clamps to one line.
 *
 * Two deliberate choices, both from things that silently did nothing here:
 *  - the collapse is CSS, not framer — framer's `animate` prop wrote no style
 *    at all to this element, so the header stayed full size over the content;
 *  - the changing values are inline styles, not conditional Tailwind classes —
 *    the classes landed on the element but no matching rule ever applied, so
 *    padding and the row collapse never moved.
 * `grid-template-rows: 1fr → 0fr` is what lets the title block animate to and
 * from its natural height without hard-coding one.
 */

/**
 * Two thresholds, not one, and the gap between them is load-bearing.
 *
 * Condensing removes ~48px of header, and the header is in flow, so the
 * document gets 48px shorter. Chrome's scroll anchoring then compensates by
 * moving scrollY back by that same 48px to keep the visible content still —
 * which, with a single threshold, dropped scrollY straight back under it.
 * The header expanded, the document grew, anchoring pushed scrollY forward,
 * it condensed again. That loop is the flicker, and because the collapse is
 * a 300ms animation rather than a jump, anchoring chased it every frame for
 * the whole transition.
 *
 * ENTER - EXIT must stay comfortably larger than the height the header
 * gives up, so an anchoring correction can never carry scrollY across the
 * other threshold. Measured collapse is ~48px; the 64px gap covers it.
 */
const CONDENSE_ENTER = 96;
const CONDENSE_EXIT = 32;
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

interface ScreenHeaderProps {
  /**
   * The position in the analytics sequence. Omitted on the employee screens,
   * which are not steps of that argument — the eyebrow stands alone there
   * rather than showing an empty slot and a stray separator.
   */
  step?: string;
  eyebrow: string;
  title: string;
  lede: string;
  /** Filter row — one row, above everything it scopes. */
  controls?: ReactNode;
  /** Receives whether the header is condensed, so the sentence can shrink too. */
  headline?: (compact: boolean) => ReactNode;
}

function useCondensed(enter = CONDENSE_ENTER, exit = CONDENSE_EXIT) {
  const [condensed, setCondensed] = useState(false);
  const current = useRef(false);

  useEffect(() => {
    // Read straight off the scroll event rather than coalescing into a
    // requestAnimationFrame. This is two number comparisons and a ref check,
    // and it calls setState only when the boolean actually flips — so the
    // throttle saved nothing measurable while making the header depend on
    // frames being produced at all, which they are not in a hidden tab.
    const onScroll = () => {
      // Hysteresis: which threshold applies depends on where we already are.
      const next = current.current ? window.scrollY > exit : window.scrollY > enter;
      if (next !== current.current) {
        current.current = next;
        setCondensed(next);
      }
    };

    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [enter, exit]);

  return condensed;
}

/**
 * Natural height of a block, kept current as it reflows.
 *
 * Needed because the `grid-template-rows: 1fr → 0fr` collapse does not work
 * here: in an auto-height grid container an `fr` track is sized to its content
 * contribution, so `0fr` computed straight back to 60.6px and the title block
 * never collapsed. Animating a measured height is unglamorous but it actually
 * moves.
 */
function useNaturalHeight<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [height, setHeight] = useState(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHeight(el.scrollHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, height] as const;
}

export function ScreenHeader({ step, eyebrow, title, lede, controls, headline }: ScreenHeaderProps) {
  const condensed = useCondensed();
  const [titleRef, naturalHeight] = useNaturalHeight<HTMLDivElement>();

  return (
    <header
      className="sticky top-0 z-30 border-b px-10"
      style={{
        paddingTop: condensed ? 12 : 24,
        paddingBottom: condensed ? 12 : 18,
        backgroundColor: condensed ? 'rgba(11,14,20,0.97)' : 'rgba(11,14,20,0.88)',
        borderBottomColor: 'var(--border)',
        backdropFilter: 'blur(20px)',
        // Keep the browser from trying to hold the view still while THIS
        // element is the thing changing size. Without it, every frame of the
        // 300ms collapse produced a scroll correction, which re-entered the
        // scroll handler and made the page judder for the whole animation.
        overflowAnchor: 'none',
        transition: `padding 300ms ${EASE}, background-color 300ms ${EASE}`,
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-10 gap-y-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-[10px] tracking-[0.16em] text-[var(--text-secondary)] uppercase">
            {step && (
              <>
                <span className="tnum">{step}</span>
                <span aria-hidden style={{ color: 'var(--border-strong)' }}>
                  /
                </span>
              </>
            )}
            <span>{eyebrow}</span>
          </p>

          <div
            style={{
              height: condensed ? 0 : naturalHeight,
              opacity: condensed ? 0 : 1,
              overflow: 'hidden',
              overflowAnchor: 'none',
              transition: `height 300ms ${EASE}, opacity 250ms ${EASE}`,
            }}
          >
            {/* Padding rather than a margin on the h1: a child's top margin
                collapses out of this box, so `scrollHeight` came back short and
                the lede got clipped by a few pixels. */}
            <div ref={titleRef} style={{ paddingTop: 8, paddingBottom: 2 }}>
              <h1 className="text-[22px] leading-tight font-semibold tracking-[-0.01em] text-[var(--text-primary)]">
                {title}
              </h1>
              <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-[var(--text-secondary)]">
                {lede}
              </p>
            </div>
          </div>
        </div>

        {controls && <div className="flex shrink-0 items-center gap-3">{controls}</div>}
      </div>

      {headline && (
        <div style={{ marginTop: condensed ? 6 : 16, transition: `margin 300ms ${EASE}` }}>
          {headline(condensed)}
        </div>
      )}
    </header>
  );
}

import type { Transition, Variants } from 'framer-motion';

/**
 * Motion is tied to state changes, never decorative. Two transitions only:
 * `glass` for content arriving/leaving, `snap` for direct UI feedback.
 */

export const EASE_GLASS = [0.22, 1, 0.36, 1] as const;

export const glass: Transition = {
  duration: 0.6,
  ease: EASE_GLASS,
};

export const snap: Transition = {
  duration: 0.22,
  ease: EASE_GLASS,
};

/**
 * The entrance for anything that holds text.
 *
 * Opacity + y, no `filter`. The spec asks for blur-in everywhere, and the
 * blurred variant is one line away:
 *
 *   hidden: { opacity: 0, y: 10, filter: 'blur(6px)' },
 *   show:   { opacity: 1, y: 0, filter: 'blur(0px)', transition: glass,
 *             transitionEnd: { filter: 'none' } },
 *
 * `transitionEnd` matters there: framer leaves the final value on the element,
 * so landing on `blur(0px)` keeps the subtree in its own composited layer, and
 * a composited layer has no subpixel antialiasing — every figure and label
 * stays permanently soft. Resetting to `none` on completion drops the layer.
 *
 * It is off because that reset only fires if the animation actually completes,
 * and the whole page renders soft if it does not. Blur stays on `blurOverlay`,
 * where the element unmounts and takes the filter with it.
 */
export const blurIn: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: glass },
  exit: { opacity: 0, y: -6, transition: { ...glass, duration: 0.3 } },
};

/**
 * Blur-in / blur-out for short-lived overlays — tooltips, the map inspector,
 * the simulator's result cards. These mount and unmount, so a filter that
 * lingers goes away with the element.
 */
export const blurOverlay: Variants = {
  hidden: { opacity: 0, y: 6, filter: 'blur(8px)' },
  show: { opacity: 1, y: 0, filter: 'blur(0px)', transition: snap },
  exit: { opacity: 0, y: 6, filter: 'blur(8px)', transition: snap },
};

/** Parent that reveals its children one after another. */
export function stagger(step = 0.06, delay = 0): Variants {
  return {
    hidden: {},
    show: { transition: { staggerChildren: step, delayChildren: delay } },
    exit: {},
  };
}

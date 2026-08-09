import { motion } from 'framer-motion';
import type { CSSProperties, ReactNode } from 'react';
import { blurIn } from '../lib/motion';

/**
 * The one card in the app.
 *
 * NOTE — do not add `backdrop-filter` here or to anything inside a surface that
 * plays the blur-in entrance. Framer leaves a non-`none` `filter` on the
 * animated ancestor, which moves the backdrop root, and Chrome then applies the
 * backdrop blur to the element's OWN content: every number and label on the
 * screen renders permanently out of focus. The glass read comes from the
 * translucent fill, the border and the inset highlight instead. `backdrop-filter`
 * is only safe on the sticky header, which has no filter-animated ancestor.
 *
 * `tone` carries data meaning only — a neutral card is the default and is what
 * every descriptive surface uses. `intensity` (0–1) scales the tint, the border
 * and the glow with the MAGNITUDE of the number the card is reporting, so a
 * one-week slip and a five-week slip do not look the same.
 */

export type Tone = 'neutral' | 'coral' | 'teal' | 'amber';

const TONE_RGB: Record<Tone, string> = {
  neutral: '148 163 184',
  coral: '240 101 79',
  teal: '45 212 191',
  amber: '245 166 35',
};

export function toneStyle(tone: Tone, intensity = 0.5): CSSProperties {
  const rgb = TONE_RGB[tone];
  const t = Math.max(0, Math.min(1, intensity));

  if (tone === 'neutral') {
    return {
      background: 'var(--bg-card)',
      borderColor: 'var(--border)',
      boxShadow: 'inset 0 1px 0 rgb(255 255 255 / 0.05), 0 24px 60px -44px rgb(0 0 0 / 0.9)',
    };
  }

  // A tint layered over the card colour — never a saturated fill.
  const tint = 0.05 + t * 0.07; // ~5% at the low end, ~12% at full magnitude
  return {
    background: `linear-gradient(rgb(${rgb} / ${tint}), rgb(${rgb} / ${tint})), rgb(19 23 34 / 0.72)`,
    borderColor: `rgb(${rgb} / ${0.2 + t * 0.25})`,
    boxShadow: `0 12px 44px -20px rgb(${rgb} / ${0.15 + t * 0.6})`,
  };
}

interface GlassCardProps {
  children: ReactNode;
  tone?: Tone;
  intensity?: number;
  className?: string;
  /** Set false for cards that are already inside an animated parent. */
  animate?: boolean;
  style?: CSSProperties;
}

export function GlassCard({
  children,
  tone = 'neutral',
  intensity = 0.5,
  className = '',
  animate = true,
  style,
}: GlassCardProps) {
  const merged = { ...toneStyle(tone, intensity), ...style };

  return (
    <motion.section
      variants={animate ? blurIn : undefined}
      className={`rounded-xl border ${className}`}
      style={merged}
    >
      {children}
    </motion.section>
  );
}

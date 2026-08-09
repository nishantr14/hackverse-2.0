import { motion } from 'framer-motion';
import { snap } from '../lib/motion';

interface SegmentedProps<T extends string> {
  /** Announced to screen readers; not rendered unless `visibleLabel` is set. */
  label: string;
  visibleLabel?: boolean;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  /** Distinguishes the sliding indicator when several controls share a page. */
  layoutId: string;
}

export function Segmented<T extends string>({
  label,
  visibleLabel = false,
  options,
  value,
  onChange,
  layoutId,
}: SegmentedProps<T>) {
  return (
    <div className="flex items-center gap-2.5">
      {visibleLabel && (
        <span className="text-[11px] tracking-[0.06em] text-[var(--text-secondary)] uppercase">
          {label}
        </span>
      )}
      <div
        role="radiogroup"
        aria-label={label}
        className="flex gap-0.5 rounded-lg border p-0.5"
        style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.8)' }}
      >
        {options.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(opt.value)}
              className="relative rounded-[6px] px-3 py-1.5 text-[12px] font-medium transition-colors"
              style={{ color: active ? 'var(--text-primary)' : 'var(--text-secondary)' }}
            >
              {active && (
                <motion.span
                  layoutId={layoutId}
                  transition={snap}
                  aria-hidden
                  className="absolute inset-0 rounded-[6px] border"
                  style={{
                    background: 'var(--ui-active)',
                    borderColor: 'var(--ui-active-border)',
                  }}
                />
              )}
              <span className="relative">{opt.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

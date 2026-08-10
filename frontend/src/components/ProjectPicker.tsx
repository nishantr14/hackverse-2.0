import { motion } from 'framer-motion';
import { snap } from '../lib/motion';
import { buildProjectPalette, colorFor } from '../lib/projectColors';

/**
 * One component out of the real list the backend forecasts.
 *
 * Lifted out of SimulatorView so the Workforce screen can staff an opening
 * against the SAME component keys the simulator prices, chosen the same way.
 * A second picker would have been a second list of what a valid component is,
 * and the two would have disagreed the first time a component appeared in one
 * and not the other.
 *
 * `palette` is optional. The Simulator colour-codes components from the spend
 * rows it has already loaded, and `colorFor` falls back to a neutral swatch
 * for anything it does not know — so a screen that has no reason to pull the
 * whole spend table just to tint a dot can leave it out.
 */
export function ProjectPicker({
  legend,
  projects,
  value,
  onChange,
  palette,
  invalid,
  hint,
}: {
  legend: string;
  projects: string[];
  value: string;
  onChange: (v: string) => void;
  palette?: ReturnType<typeof buildProjectPalette>;
  invalid?: string | null;
  /** Small note under the legend — used where the list needs explaining. */
  hint?: string;
}) {
  const swatch = palette ?? EMPTY_PALETTE;

  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {legend}
      </legend>
      {hint && <p className="mb-2 text-[11.5px] text-[var(--text-muted)]">{hint}</p>}
      <div className="flex flex-wrap gap-1.5">
        {projects.map((p) => {
          const active = p === value;
          const c = colorFor(swatch, p);
          const bad = invalid === p;
          return (
            <motion.button
              key={p}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(p)}
              transition={snap}
              className="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors"
              style={{
                borderColor: bad
                  ? 'rgb(240 101 79 / 0.5)'
                  : active
                    ? `rgb(${c.rgb} / 0.7)`
                    : 'var(--border)',
                background: active ? `rgb(${c.rgb} / 0.14)` : 'transparent',
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
              }}
            >
              <span
                aria-hidden
                className="block h-2 w-2 shrink-0 rounded-full"
                style={{ background: c.base, opacity: active ? 1 : 0.5 }}
              />
              {p}
            </motion.button>
          );
        })}
      </div>
    </fieldset>
  );
}

/** Shared empty map, so an omitted palette is not a new identity each render. */
const EMPTY_PALETTE: ReturnType<typeof buildProjectPalette> = new Map();

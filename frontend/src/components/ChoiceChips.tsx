import { IconCheck } from './Icons';

/**
 * Chip pickers for the preference form.
 *
 * `Segmented` already covers a small single-choice control, but it is sized
 * for a toolbar and has no multi-select mode. These are the same chip shape
 * the simulator's project picker uses, generalised so one field type reads
 * identically whether it takes one answer or several.
 *
 * Single-choice is a real radiogroup and multi-choice is a group of toggle
 * buttons with aria-pressed — not the same control with a flag, because a
 * screen reader has to be told which one it is. Arrow-key roving is left to
 * the browser's native radio behaviour rather than reimplemented.
 */

interface Option<T extends string> {
  value: T;
  label: string;
}

const chipStyle = (active: boolean) => ({
  borderColor: active ? 'var(--ui-active-border)' : 'var(--border)',
  background: active ? 'var(--ui-active)' : 'transparent',
  color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
});

const CHIP =
  'flex items-center gap-1.5 rounded-lg border px-3 py-2 text-[12.5px] transition-colors hover:border-[var(--border-strong)]';

function Legend({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <legend className="mb-2 flex flex-wrap items-baseline gap-x-2">
      <span className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {children}
      </span>
      {hint && <span className="text-[11px] text-[var(--text-muted)]">{hint}</span>}
    </legend>
  );
}

export function ChoiceChips<T extends string>({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: readonly Option<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <fieldset className="min-w-0">
      <Legend>{legend}</Legend>
      <div role="radiogroup" aria-label={legend} className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const active = o.value === value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(o.value)}
              className={CHIP}
              style={chipStyle(active)}
            >
              {active && <IconCheck className="h-3 w-3 shrink-0" />}
              {o.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

export function MultiChoiceChips<T extends string>({
  legend,
  hint,
  options,
  value,
  onChange,
}: {
  legend: string;
  hint?: string;
  options: readonly Option<T>[];
  value: readonly T[];
  onChange: (v: T[]) => void;
}) {
  function toggle(v: T) {
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  }

  return (
    <fieldset className="min-w-0">
      <Legend hint={hint}>{legend}</Legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const active = value.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={active}
              onClick={() => toggle(o.value)}
              className={CHIP}
              style={chipStyle(active)}
            >
              {active && <IconCheck className="h-3 w-3 shrink-0" />}
              {o.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

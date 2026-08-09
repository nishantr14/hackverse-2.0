import { useState, type ReactNode } from 'react';

/**
 * Text, number and tag inputs in the existing chip language.
 *
 * ChoiceChips already covers the closed vocabularies (shift, weekday, work
 * area). These cover the open ones — a skill list, a city, a number of years —
 * which the profile needs and the preference form never did. Same border, same
 * radius, same type scale, so the two halves of a form do not look like they
 * came from different products.
 */

const FIELD_STYLE = {
  background: 'var(--bg-raised)',
  borderColor: 'var(--border)',
  color: 'var(--text-primary)',
};

function Legend({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {label}
      </span>
      {hint && <span className="text-[11px] text-[var(--text-muted)]">{hint}</span>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Legend label={label} hint={hint} />
      {children}
    </div>
  );
}

export function TextField({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-lg border px-3 text-[13px] outline-none transition-colors focus:border-[var(--border-strong)]"
        style={FIELD_STYLE}
      />
    </Field>
  );
}

export function NumberField({
  label,
  hint,
  value,
  onChange,
  min = 0,
  max = 60,
  prefix,
  suffix,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  prefix?: string;
  suffix?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <div
        className="flex h-9 items-center gap-1.5 rounded-lg border px-3 transition-colors focus-within:border-[var(--border-strong)]"
        style={FIELD_STYLE}
      >
        {prefix && <span className="text-[13px] text-[var(--text-muted)]">{prefix}</span>}
        <input
          type="number"
          value={Number.isFinite(value) ? value : 0}
          min={min}
          max={max}
          // Clamped on the way in: a negative relocation package would flip the
          // sign of the net impact and read as a saving.
          onChange={(e) => {
            const n = Number(e.target.value);
            onChange(Math.max(min, Math.min(max, Number.isFinite(n) ? n : min)));
          }}
          className="tnum w-full bg-transparent text-[13px] outline-none"
          style={{ color: 'var(--text-primary)' }}
        />
        {suffix && <span className="text-[13px] text-[var(--text-muted)]">{suffix}</span>}
      </div>
    </Field>
  );
}

/**
 * A free list — skills, cities, certifications.
 *
 * Enter or comma commits; backspace on an empty box removes the last one. No
 * autocomplete, because there is no canonical vocabulary to complete against
 * and inventing one would quietly constrain what an employee can say about
 * themselves.
 */
export function TagField({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint?: string;
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState('');

  function commit() {
    const next = draft.trim().replace(/,$/, '');
    if (next && !value.some((v) => v.toLowerCase() === next.toLowerCase())) {
      onChange([...value, next]);
    }
    setDraft('');
  }

  return (
    <Field label={label} hint={hint}>
      <div
        className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border px-2 py-1.5 transition-colors focus-within:border-[var(--border-strong)]"
        style={FIELD_STYLE}
      >
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[12px]"
            style={{
              borderColor: 'var(--border-strong)',
              background: 'var(--ui-hover)',
              color: 'var(--text-primary)',
            }}
          >
            {tag}
            <button
              type="button"
              onClick={() => onChange(value.filter((v) => v !== tag))}
              aria-label={`Remove ${tag}`}
              className="text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          placeholder={value.length ? '' : placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault();
              commit();
            } else if (e.key === 'Backspace' && !draft && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
          onBlur={commit}
          className="min-w-[8rem] flex-1 bg-transparent px-1 text-[13px] outline-none"
          style={{ color: 'var(--text-primary)' }}
        />
      </div>
    </Field>
  );
}

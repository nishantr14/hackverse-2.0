import { NumberField } from './Fields';
import type { RelocationAssumptions } from '../data/types';
import { ASSUMPTION_FIELDS } from '../lib/reallocation';

/**
 * The four numbers the event log cannot know.
 *
 * Nothing in a commit, a review or a CI run says what it costs to move somebody
 * to another city, so these are typed in — and the panel is titled and framed
 * so they can never be mistaken for measurements. That framing is the whole
 * job of this component: the rest of the product spends its effort proving
 * numbers are traceable, and the fastest way to undo that is to slip four
 * invented ones in beside them without a label.
 *
 * They are editable rather than constant because the honest answer to "what
 * does relocation cost" is "it depends, and you know your business better than
 * we do". A director changing them and watching the verdict flip is a better
 * argument than any default we could pick.
 */
export function ScenarioAssumptions({
  value,
  onChange,
  disabled,
}: {
  value: RelocationAssumptions;
  onChange: (next: RelocationAssumptions) => void;
  disabled?: boolean;
}) {
  return (
    <div
      className="rounded-xl border p-5"
      style={{
        borderColor: 'rgb(245 166 35 / 0.3)',
        background: 'rgb(245 166 35 / 0.04)',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h3 className="text-[13px] font-semibold" style={{ color: 'var(--amber)' }}>
          Scenario assumptions
        </h3>
        <p className="text-[11.5px] text-[var(--text-muted)]">
          Typed in, not observed. Change them and the verdict changes.
        </p>
      </div>

      <div className="mt-4 grid gap-x-6 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
        {ASSUMPTION_FIELDS.map((f) => (
          <NumberField
            key={f.key}
            label={f.label}
            hint={f.hint}
            value={value[f.key]}
            onChange={(n) => onChange({ ...value, [f.key]: n })}
            min={0}
            max={f.kind === 'rupees' ? 5000000 : 52}
            prefix={f.kind === 'rupees' ? '₹' : undefined}
            suffix={f.kind === 'weeks' ? 'weeks' : undefined}
          />
        ))}
      </div>

      <p className="mt-4 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
        None of these is a company figure. They are inputs to this scenario only, and they are kept
        apart from the simulated delivery impact all the way to the total below — the simulator
        already applies its own ramp-up penalty, so the ramp-up here is{' '}
        <strong className="text-[var(--text-secondary)]">additional to it, never instead of it</strong>.
      </p>
    </div>
  );
}

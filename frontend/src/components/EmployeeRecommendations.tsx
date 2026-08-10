import { motion } from 'framer-motion';

import { GlassCard } from './GlassCard';
import {
  FIT_DIMENSION_SHORT_LABEL,
  FIT_DIMENSIONS,
  SHIFT_LABEL,
  WEEKDAY_LABEL,
  WORK_AREA_LABEL,
} from '../lib/workforce';
import type { EmployeeRecommendation, FitDimension, WorkforceRecommendationSet } from '../data/types';

/**
 * WHO SHOULD WE MOVE, AND WHY — the block that sits above projected impact.
 *
 * The order on screen is the order of the argument: who, then why, then what
 * happens if we do it. Putting the cost first would make the people look like
 * a footnote to a number, when the whole point of the feature is that a
 * reallocation is a decision about named people who told us how they want to
 * work.
 *
 * NOTHING HERE RENDERS A PERFORMANCE FIGURE, because the payload carries
 * none. Every line is either something the employee declared or something
 * their resume says.
 */

// Labels and order come from lib/workforce so the PDF export renders the
// same five dimensions under the same names. A local copy here is how a
// printed page and the card it was exported from start disagreeing.

function Bar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div
      className="h-1.5 flex-1 overflow-hidden rounded-full"
      style={{ background: 'var(--ui-active)' }}
    >
      <div
        className="h-full rounded-full"
        style={{
          width: `${pct * 100}%`,
          background: 'var(--accent, rgb(99,102,241))',
          transition: 'width 0.5s cubic-bezier(0.22,1,0.36,1)',
        }}
      />
    </div>
  );
}

function Card({
  rec,
  rank,
  weights,
}: {
  rec: EmployeeRecommendation;
  rank: number;
  weights: Record<FitDimension, number>;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: rank * 0.06 }}
    >
      <GlassCard className="h-full p-5" animate={false}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[14px] font-semibold text-[var(--text-primary)]">{rec.name}</p>
            <p className="mt-0.5 text-[11px] text-[var(--text-secondary)]">{rec.employeeId}</p>
          </div>
          <p className="tnum text-[26px] leading-none font-semibold text-[var(--text-primary)]">
            {rec.matchPercent}%
          </p>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {rec.skills.map((s) => (
            <span
              key={s}
              className="rounded-full px-2 py-0.5 text-[11px]"
              style={{
                background: rec.matchedSkills.includes(s) ? 'var(--ui-active)' : 'transparent',
                border: '1px solid var(--border)',
                color: rec.matchedSkills.includes(s)
                  ? 'var(--text-primary)'
                  : 'var(--text-secondary)',
              }}
            >
              {s}
            </span>
          ))}
        </div>

        {/* The weighted contributions ARE the explanation — each bar is that
            dimension's share of the headline, drawn against its own weight so
            a full 10% term looks full rather than small. */}
        <div className="mt-4 space-y-1.5">
          {FIT_DIMENSIONS.map((dim) => (
            <div key={dim} className="flex items-center gap-2">
              <span className="w-[5.5rem] text-[11px] text-[var(--text-secondary)]">
                {FIT_DIMENSION_SHORT_LABEL[dim]}
              </span>
              <Bar value={rec.contributions[dim]} max={weights[dim]} />
              <span className="tnum w-9 text-right text-[11px] text-[var(--text-secondary)]">
                {Math.round(rec.subScores[dim] * 100)}%
              </span>
            </div>
          ))}
        </div>

        <div className="mt-4 space-y-1">
          {rec.reasons.map((r) => (
            <p key={r} className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              <span style={{ color: 'var(--text-primary)' }}>✓</span> {r}
            </p>
          ))}
          {rec.flags.map((f) => (
            <p key={f} className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              <span>⚠</span> {f}
            </p>
          ))}
        </div>
      </GlassCard>
    </motion.div>
  );
}

export function EmployeeRecommendations({ set }: { set: WorkforceRecommendationSet }) {
  const { requirement: req } = set;
  return (
    <div className="space-y-3">
      <GlassCard className="p-5" animate={false}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-[14px] font-semibold text-[var(--text-primary)]">
            Who should we move?
          </p>
          {/* Never presented as something a person submitted. Same badge
              discipline as modelled meeting time and token volume. */}
          <span
            className="rounded-full px-2.5 py-0.5 text-[11px]"
            style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          >
            {set.dataBasis.label}
          </span>
        </div>
        <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
          {req.thin ? (
            <>
              <strong style={{ color: 'var(--text-primary)' }}>{req.component}</strong> carries no
              skill signal, so the requirement falls back to the repository language only and the
              skill term is weak evidence here.
            </>
          ) : (
            <>
              {req.component} needs {req.requiredSkills.join(', ')}
              {req.workAreas.length > 0 && (
                <> · {req.workAreas.map((a) => WORK_AREA_LABEL[a]).join(', ')}</>
              )}{' '}
              · {SHIFT_LABEL[req.preferredShift]} ·{' '}
              {req.requiredAvailability.map((d) => WEEKDAY_LABEL[d].slice(0, 3)).join(', ')}
            </>
          )}
        </p>
        <p className="mt-1.5 text-[11.5px] text-[var(--text-secondary)]">{set.privacyBasis}</p>
      </GlassCard>

      <div className="grid gap-3 md:grid-cols-2">
        {set.recommendedEmployees.map((rec, i) => (
          <Card key={rec.employeeId} rec={rec} rank={i} weights={set.weights} />
        ))}
      </div>

      {set.alternates.length > 0 && (
        <details>
          <summary className="cursor-pointer text-[12px] text-[var(--text-secondary)]">
            {set.alternates.length} alternate{set.alternates.length === 1 ? '' : 's'}
          </summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {set.alternates.map((rec, i) => (
              <Card key={rec.employeeId} rec={rec} rank={i} weights={set.weights} />
            ))}
          </div>
        </details>
      )}

      <GlassCard className="p-5" animate={false}>
        <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
          <strong style={{ color: 'var(--text-primary)' }}>
            {set.anonymousCapacity.count} more
          </strong>{' '}
          {set.anonymousCapacity.count === 1 ? 'profile has' : 'profiles have'} no submitted
          preference record. {set.anonymousCapacity.note}
        </p>
        {set.excluded.length > 0 && (
          <div className="mt-3 space-y-1">
            {set.excluded.map((e) => (
              <p key={e.employeeId} className="text-[11.5px] text-[var(--text-secondary)]">
                <strong style={{ color: 'var(--text-primary)' }}>{e.name}</strong> — {e.reason}
              </p>
            ))}
          </div>
        )}
        {/* Step 9. The controls say what they are: nothing here commits. */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {set.humanInTheLoop.actions.map((a) => (
            <button
              key={a}
              type="button"
              className="h-9 rounded-lg border px-4 text-[13px]"
              style={{ color: 'var(--text-secondary)', borderColor: 'var(--border)' }}
            >
              {a}
            </button>
          ))}
          <span className="text-[11.5px] text-[var(--text-secondary)]">
            {set.humanInTheLoop.note}
          </span>
        </div>
      </GlassCard>
    </div>
  );
}

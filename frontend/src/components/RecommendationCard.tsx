import { AnimatePresence, motion } from 'framer-motion';
import { useId, useState } from 'react';
import { EASE_GLASS } from '../lib/motion';
import { SHIFT_LABEL, WEEKDAY_LABEL, WORK_STYLE_LABEL } from '../lib/workforce';
import type { Recommendation, RecommendationEvidence } from '../data/types';
import { GlassCard } from './GlassCard';
import { IconCheck, IconChevron } from './Icons';

/**
 * One candidate for one opening.
 *
 * The match figure is deliberately not the loudest thing on the card. A
 * percentage next to a person's name invites being read as a rating of them,
 * so the card leads with the name, keeps the number to a supporting size, and
 * puts the sentence explaining it directly underneath. What the card is really
 * for is the evidence disclosure — the number is an entry point to the
 * reasoning, not a verdict.
 *
 * `preferenceMatch` and `availabilityMatch` render as met/unmet rather than as
 * a green tick or nothing, because an absent tick is ambiguous: it could mean
 * "not met" or "not known", and those are different facts.
 */

const REVIEW_THRESHOLD = 60;

function Pill({ children, met }: { children: React.ReactNode; met: boolean }) {
  return (
    <span
      className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px]"
      style={{
        borderColor: met ? 'rgb(45 212 191 / 0.35)' : 'var(--border)',
        background: met ? 'rgb(45 212 191 / 0.08)' : 'transparent',
        color: met ? 'var(--teal)' : 'var(--text-muted)',
      }}
    >
      {met ? (
        <IconCheck className="h-3 w-3 shrink-0" />
      ) : (
        <span aria-hidden className="block h-[1.5px] w-2.5 shrink-0 rounded-full bg-current" />
      )}
      {children}
    </span>
  );
}

function EvidenceGroup({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {title}
      </p>
      <ul className="mt-2 flex flex-col gap-1.5">
        {items.map((item) => (
          <li
            key={item}
            className="flex gap-2.5 text-[12.5px] leading-relaxed text-[var(--text-primary)]"
          >
            <span
              aria-hidden
              className="mt-[7px] block h-1 w-1 shrink-0 rounded-full"
              style={{ background: 'var(--border-strong)' }}
            />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The evidence disclosure, split out so the director's richer candidate card
 * shows exactly the same four groups. Two components rendering "the evidence"
 * differently would be a way for one of them to quietly show less.
 */
export function EvidencePanel({ ev }: { ev: RecommendationEvidence }) {
  return (
    <div
      className="grid gap-6 rounded-xl border p-5 sm:grid-cols-2"
      style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.6)' }}
    >
      <EvidenceGroup
        title="From the resume"
        items={[...ev.resume.projects, ...ev.resume.experience]}
      />
      <EvidenceGroup title="Technical skills on file" items={ev.resume.skills} />
      <EvidenceGroup
        title="What the employee told us"
        items={[
          `Preferred shift: ${SHIFT_LABEL[ev.preferences.preferredShift]}`,
          `Works best: ${WORK_STYLE_LABEL[ev.preferences.workStyle]}`,
          `Available: ${ev.preferences.availability.map((d) => WEEKDAY_LABEL[d]).join(', ')}`,
        ]}
      />
      <EvidenceGroup
        title="What the opening needs"
        items={[
          `Skills: ${ev.requirement.requiredSkills.join(', ')}`,
          `Shift: ${SHIFT_LABEL[ev.requirement.requiredShift]}`,
          `Days: ${ev.requirement.requiredAvailability.map((d) => WEEKDAY_LABEL[d]).join(', ')}`,
        ]}
      />
      <div className="sm:col-span-2">
        <EvidenceGroup title="Policy applied" items={ev.policies} />
      </div>
    </div>
  );
}

export { Pill };

export function RecommendationCard({ rec, rank }: { rec: Recommendation; rank: number }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const belowThreshold = rec.match < REVIEW_THRESHOLD;
  const ev = rec.evidence;

  return (
    <GlassCard className="p-5" animate={false}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2.5">
            <span className="tnum text-[11px] text-[var(--text-muted)]">
              {String(rank).padStart(2, '0')}
            </span>
            <span className="text-[15px] font-semibold text-[var(--text-primary)]">
              {rec.employee}
            </span>
          </p>
          <p className="mt-2 max-w-xl text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
            {rec.reason}
          </p>
        </div>

        <div className="shrink-0 text-right">
          <p
            className="tnum text-[26px] leading-none font-semibold"
            style={{ color: belowThreshold ? 'var(--text-muted)' : 'var(--text-primary)' }}
          >
            {rec.match}%
          </p>
          <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">match to this opening</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        {rec.skills.map((s) => (
          <Pill key={s} met>
            {s}
          </Pill>
        ))}
        <Pill met={rec.preferenceMatch}>
          {rec.preferenceMatch ? 'Preference match' : 'Preference not met'}
        </Pill>
        <Pill met={rec.availabilityMatch}>
          {rec.availabilityMatch ? 'Availability match' : 'Availability not met'}
        </Pill>
      </div>

      <div
        className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t pt-3.5"
        style={{ borderColor: 'var(--border)' }}
      >
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition-colors"
          style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
        >
          {/* Rotation, not two icons: the control is one thing in two states. */}
          <span
            aria-hidden
            className="flex shrink-0 transition-transform duration-200"
            style={{ transform: open ? 'rotate(180deg)' : 'none' }}
          >
            <IconChevron className="h-3.5 w-3.5" />
          </span>
          {open ? 'Hide the evidence' : 'Why this recommendation?'}
        </button>

        {belowThreshold && (
          <span className="text-[11.5px] text-[var(--text-muted)]">
            Below the review threshold — shown so you can see who was considered.
          </span>
        )}
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={panelId}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: EASE_GLASS }}
            style={{ overflow: 'hidden' }}
          >
            <div className="mt-4">
              <EvidencePanel ev={ev} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </GlassCard>
  );
}

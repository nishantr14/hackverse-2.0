import { AnimatePresence, motion } from 'framer-motion';
import { useId, useState } from 'react';
import type { Candidate, Opening } from '../data/types';
import { EASE_GLASS } from '../lib/motion';
import { FEASIBILITY_LABEL, FEASIBILITY_TONE, feasibility } from '../lib/reallocation';
import { GlassCard } from './GlassCard';
import { IconCheck, IconChevron } from './Icons';
import { EvidencePanel } from './RecommendationCard';
import { WhyBreakdown } from './WhyBreakdown';

/**
 * A candidate for one opening, in the director's view.
 *
 * The richer sibling of RecommendationCard: same evidence disclosure, plus the
 * four things a staffing decision actually turns on and a preference form does
 * not carry — where they are, whether they would move, what they are carrying
 * now, and how the fit figure was arrived at.
 *
 * THE SCORE IS NOT THE HEADLINE. A percentage next to a person's name reads as
 * a rating of the person, so the name leads, the figure supports, and the
 * sentence explaining it sits directly beneath. What the card is for is the
 * disclosure underneath, not the number on top.
 *
 * Nothing here is derived from the event log. The workload badge is volunteered,
 * the location is volunteered, and there is deliberately no cost, no output
 * measure and no comparison to a colleague on this card.
 */

const REVIEW_THRESHOLD = 60;

const WORKLOAD_LABEL: Record<Candidate['currentWorkload'], string> = {
  light: 'Light current workload',
  normal: 'Normal current workload',
  heavy: 'Heavy current workload',
};

function Chip({
  children,
  tone = 'neutral',
}: {
  children: React.ReactNode;
  tone?: 'neutral' | 'teal' | 'amber' | 'coral';
}) {
  const map = {
    neutral: { border: 'var(--border)', bg: 'transparent', fg: 'var(--text-secondary)' },
    teal: { border: 'rgb(45 212 191 / 0.35)', bg: 'rgb(45 212 191 / 0.08)', fg: 'var(--teal)' },
    amber: { border: 'rgb(245 166 35 / 0.35)', bg: 'rgb(245 166 35 / 0.08)', fg: 'var(--amber)' },
    coral: { border: 'rgb(240 101 79 / 0.35)', bg: 'rgb(240 101 79 / 0.08)', fg: 'var(--coral)' },
  }[tone];

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px]"
      style={{ borderColor: map.border, background: map.bg, color: map.fg }}
    >
      {children}
    </span>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10.5px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {label}
      </dt>
      <dd className="mt-1 text-[12.5px] text-[var(--text-primary)]">{value}</dd>
    </div>
  );
}

export function CandidateCard({
  candidate,
  opening,
  rank,
  selected,
  onSelect,
  compared,
  onToggleCompare,
}: {
  candidate: Candidate;
  opening: Opening;
  rank: number;
  selected: boolean;
  onSelect: () => void;
  compared: boolean;
  onToggleCompare: () => void;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const belowThreshold = candidate.match < REVIEW_THRESHOLD;
  const f = feasibility(candidate, opening);
  const blocked = f === 'not-opted-in';

  return (
    <GlassCard
      className="p-5"
      animate={false}
      style={
        selected
          ? { borderColor: 'var(--border-strong)', background: 'rgb(30 36 50 / 0.72)' }
          : undefined
      }
    >
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="tnum text-[11px] text-[var(--text-muted)]">
              {String(rank).padStart(2, '0')}
            </span>
            <span className="text-[15px] font-semibold text-[var(--text-primary)]">
              {candidate.employee}
            </span>
            <span className="text-[12px] text-[var(--text-secondary)]">
              {candidate.primaryRole} · {candidate.experienceYears} yrs
            </span>
          </p>
          <p className="mt-2 max-w-xl text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
            {candidate.reason}
          </p>
        </div>

        <div className="shrink-0 text-right">
          <p
            className="tnum text-[26px] leading-none font-semibold"
            style={{ color: belowThreshold ? 'var(--text-muted)' : 'var(--text-primary)' }}
          >
            {candidate.match}%
          </p>
          <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">suitability for this opening</p>
        </div>
      </div>

      <dl className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-4">
        <Fact label="Current location" value={candidate.currentLocation} />
        <Fact label="Currently on" value={candidate.currentComponentLabel} />
        <Fact label="Opening is in" value={opening.location} />
        <Fact
          label="Relocation"
          value={candidate.openToRelocation ? 'Open to relocating' : 'Not opted in'}
        />
      </dl>

      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        {candidate.skills.map((s) => (
          <Chip key={s} tone="teal">
            <IconCheck className="h-3 w-3 shrink-0" />
            {s}
          </Chip>
        ))}
        <Chip tone={FEASIBILITY_TONE[f]}>{FEASIBILITY_LABEL[f]}</Chip>
        <Chip tone={candidate.currentWorkload === 'heavy' ? 'amber' : 'neutral'}>
          {WORKLOAD_LABEL[candidate.currentWorkload]}
        </Chip>
      </div>

      <div
        className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t pt-3.5"
        style={{ borderColor: 'var(--border)' }}
      >
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition-colors"
            style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
          >
            <span
              aria-hidden
              className="flex shrink-0 transition-transform duration-200"
              style={{ transform: open ? 'rotate(180deg)' : 'none' }}
            >
              <IconChevron className="h-3.5 w-3.5" />
            </span>
            {open ? 'Hide the reasoning' : 'Why?'}
          </button>

          <button
            type="button"
            onClick={onToggleCompare}
            aria-pressed={compared}
            className="rounded-lg border px-3 py-1.5 text-[12px] transition-colors"
            style={{
              borderColor: compared ? 'var(--border-strong)' : 'var(--border)',
              background: compared ? 'var(--ui-active)' : 'transparent',
              color: compared ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
          >
            {compared ? 'In comparison' : 'Compare'}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {belowThreshold && (
            <span className="text-[11.5px] text-[var(--text-muted)]">
              Below the review threshold — shown so you can see who was considered.
            </span>
          )}
          <button
            type="button"
            onClick={onSelect}
            disabled={blocked}
            title={
              blocked
                ? 'This candidate has not opted in to relocation, and this opening requires it.'
                : undefined
            }
            className="h-8 rounded-lg border px-3.5 text-[12px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            style={
              selected
                ? { color: 'var(--bg-page)', background: 'var(--ui)', borderColor: 'var(--ui)' }
                : { color: 'var(--text-primary)', borderColor: 'var(--border-strong)' }
            }
          >
            {selected ? 'Selected for scenario' : 'Select for scenario'}
          </button>
        </div>
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
            <div className="mt-4 flex flex-col gap-4">
              <WhyBreakdown contributions={candidate.contributions} total={candidate.match} />
              <EvidencePanel ev={candidate.evidence} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </GlassCard>
  );
}

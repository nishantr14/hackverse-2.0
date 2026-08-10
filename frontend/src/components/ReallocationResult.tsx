import { motion } from 'framer-motion';
import type { Candidate, Opening, SimulatorOutput } from '../data/types';
import { formatMoney, formatMoneyDelta } from '../lib/format';
import { EASE_GLASS } from '../lib/motion';
import { FEASIBILITY_LABEL, type NetImpact, type ProjectOutcome } from '../lib/reallocation';
import { bandVerdict, confidenceShape } from '../lib/simulator';
import { GlassCard } from './GlassCard';

/**
 * What a reallocation would actually do.
 *
 * Three panels in the order a decision is made: the headcount that moves, what
 * it costs once BOTH projects are priced, and then the verdict with its
 * reasoning attached. The verdict is last on purpose — a PROCEED badge at the
 * top would be read first and the working ignored.
 *
 * PROVENANCE IS RENDERED, NOT ASSUMED. Every cost line carries a tag saying
 * whether it came from the simulator, from a text box, or from arithmetic over
 * an observed rate. A screen that mixes the three without saying so is how a
 * typed-in guess ends up quoted in a board meeting as a measurement.
 */

const PROVENANCE_LABEL = {
  simulated: 'simulated',
  assumption: 'assumption',
  derived: 'derived',
} as const;

const PROVENANCE_TONE = {
  simulated: 'var(--teal)',
  assumption: 'var(--amber)',
  derived: 'var(--text-secondary)',
} as const;

const STATUS_STYLE: Record<ProjectOutcome['status'], { dot: string; label: string }> = {
  improved: { dot: 'var(--teal)', label: 'improved' },
  delayed: { dot: 'var(--amber)', label: 'delayed' },
  'not-modelled': { dot: 'var(--border-strong)', label: 'not modelled' },
};

function HeadcountPair({
  label,
  before,
  after,
}: {
  label: string;
  before: number | null;
  after: number | null;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {label}
      </span>
      {before === null || after === null ? (
        <span className="text-[13px] text-[var(--text-muted)]">Headcount not available</span>
      ) : (
        <span className="flex items-baseline gap-2">
          <span className="tnum text-[22px] font-semibold text-[var(--text-muted)]">{before}</span>
          <span aria-hidden className="text-[var(--border-strong)]">
            →
          </span>
          <span className="tnum text-[22px] font-semibold text-[var(--text-primary)]">{after}</span>
          <span className="text-[11.5px] text-[var(--text-muted)]">engineers</span>
        </span>
      )}
    </div>
  );
}

export function ReallocationResult({
  candidate,
  opening,
  sim,
  impact,
  outcomes,
  sourceHeadcount,
  destHeadcount,
}: {
  candidate: Candidate;
  opening: Opening;
  sim: SimulatorOutput;
  impact: NetImpact;
  outcomes: ProjectOutcome[];
  sourceHeadcount: number | null;
  destHeadcount: number | null;
}) {
  const proceed = impact.verdict === 'proceed';
  // Same derivation as the Simulator's band, from the same two fields.
  const conf = confidenceShape(sim);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: EASE_GLASS }}
      className="flex flex-col gap-4"
    >
      {/* 1 — who moves where */}
      <GlassCard className="p-5" animate={false}>
        <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">
          Current state → proposed
        </h3>
        <p className="mt-1.5 text-[12.5px] text-[var(--text-secondary)]">
          Moving one engineer from {candidate.currentComponentLabel} to {opening.project} /{' '}
          {opening.component}. Headcount is observed from the event log; who moves is not.
        </p>

        <div className="mt-5 grid gap-6 sm:grid-cols-2">
          <HeadcountPair
            label={`${opening.project} / ${opening.component} (destination)`}
            before={destHeadcount}
            after={destHeadcount === null ? null : destHeadcount + 1}
          />
          <HeadcountPair
            label={`${candidate.currentComponentLabel} (source)`}
            before={sourceHeadcount}
            after={sourceHeadcount === null ? null : sourceHeadcount - 1}
          />
        </div>

        <div
          className="mt-5 grid gap-4 border-t pt-4 sm:grid-cols-3"
          style={{ borderColor: 'var(--border)' }}
        >
          <div>
            <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
              Destination delivery
            </p>
            <p
              className="tnum mt-1 text-[16px] font-semibold"
              style={{ color: sim.destDeltaWeeks < 0 ? 'var(--teal)' : 'var(--text-primary)' }}
            >
              {sim.destDeltaWeeks > 0 ? '+' : ''}
              {sim.destDeltaWeeks.toFixed(1)} weeks
            </p>
          </div>
          <div>
            <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
              Source delivery
            </p>
            <p
              className="tnum mt-1 text-[16px] font-semibold"
              style={{ color: sim.sourceDeltaWeeks > 0 ? 'var(--coral)' : 'var(--text-primary)' }}
            >
              {sim.sourceDeltaWeeks > 0 ? '+' : ''}
              {sim.sourceDeltaWeeks.toFixed(1)} weeks
            </p>
          </div>
          <div>
            <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
              Confidence band
            </p>
            {/* P10–P90, the same readout the Simulator gives, from the same
                two fields. This printed `±confidencePercent` — a confidence
                LEVEL rendered as a tolerance, so a band of 32–68 was reported
                as "±63.9%", a number that is neither the width nor a bound. */}
            <p className="tnum mt-1 text-[16px] font-semibold text-[var(--text-primary)]">
              {sim.confidenceLow}–{sim.confidenceHigh}%
            </p>
            <p className="mt-1 text-[11px] text-[var(--text-muted)]">
              {bandVerdict(conf)} · {conf.spread.toFixed(1)} points wide
              {sim.confidencePercent !== undefined && ` · ${sim.confidencePercent}% confidence`}
            </p>
          </div>
        </div>
      </GlassCard>

      {/* 2 — the money, line by line, with where each line came from */}
      <GlassCard className="p-5" animate={false}>
        <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">
          What it costs, both halves
        </h3>

        <ul className="mt-4 flex flex-col">
          {impact.lines.map((line) => (
            <li
              key={line.label}
              className="flex flex-wrap items-start justify-between gap-x-6 gap-y-1 border-b py-3 last:border-b-0"
              style={{ borderColor: 'var(--border)' }}
            >
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-[13px] text-[var(--text-primary)]">{line.label}</span>
                  <span
                    className="rounded border px-1.5 py-0.5 text-[10px] tracking-[0.06em] uppercase"
                    style={{
                      borderColor: PROVENANCE_TONE[line.provenance],
                      color: PROVENANCE_TONE[line.provenance],
                    }}
                  >
                    {PROVENANCE_LABEL[line.provenance]}
                  </span>
                </p>
                <p className="mt-1 max-w-2xl text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                  {line.note}
                </p>
              </div>
              <span
                className="tnum shrink-0 text-[14px] font-semibold"
                style={{ color: line.rupees > 0 ? 'var(--coral)' : 'var(--teal)' }}
              >
                {line.rupees > 0 ? '−' : '+'}
                {formatMoney(Math.abs(line.rupees))}
              </span>
            </li>
          ))}
        </ul>

        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t pt-4"
          style={{ borderColor: 'var(--border-strong)' }}
        >
          <div>
            {/* NEUTRAL HEADING, SIGNED FIGURE — the same pairing as every line
                above, where "Relocation package" carries no direction and the
                −₹1L beside it carries all of it.

                This used to read "Net expected cost" over "−₹3.3L", which is a
                double negative: a cost of minus 3.3 lakh is a saving, and the
                move it described costs 3.3 lakh. Naming the direction in the
                heading AND in the sign meant the two could disagree, so the
                heading stops naming it and the sentence below says it in
                words instead. */}
            <p className="text-[12px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
              Net expected impact
            </p>
            <p className="mt-1 text-[11.5px] text-[var(--text-muted)]">
              {impact.netBenefitRupees >= 0
                ? `This move saves ${formatMoney(Math.abs(impact.netBenefitRupees))} overall.`
                : `This move costs ${formatMoney(Math.abs(impact.netBenefitRupees))} overall.`}{' '}
              Simulated delivery impact plus the scenario assumptions above.
            </p>
          </div>
          {/* Same convention as the lines above it: − is money leaving. The
              total previously negated this and read "+₹3.3L" for a cost while
              every line beneath it wrote a cost as "−". */}
          <span
            className="tnum text-[28px] leading-none font-semibold"
            style={{ color: impact.netBenefitRupees >= 0 ? 'var(--teal)' : 'var(--coral)' }}
          >
            {formatMoneyDelta(impact.netBenefitRupees)}
          </span>
        </div>
      </GlassCard>

      {/* 3 — the whole system, then the call */}
      <GlassCard className="p-5" animate={false}>
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">
            Effect on the system
          </h3>
          <p className="text-[11.5px] text-[var(--text-muted)]">
            Two components are modelled. The rest are not claimed to be unaffected.
          </p>
        </div>

        <ul className="mt-4 flex flex-col gap-2">
          {outcomes.map((o) => (
            <li
              key={o.key}
              className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 rounded-lg border px-3.5 py-2.5"
              style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)' }}
            >
              <span className="flex items-center gap-2.5">
                <span
                  aria-hidden
                  className="block h-2 w-2 shrink-0 rounded-full"
                  style={{ background: STATUS_STYLE[o.status].dot }}
                />
                <span className="text-[13px] text-[var(--text-primary)]">{o.label}</span>
                <span className="text-[11.5px] text-[var(--text-muted)]">
                  {STATUS_STYLE[o.status].label}
                </span>
              </span>
              <span className="text-[12px] text-[var(--text-secondary)]">{o.detail}</span>
            </li>
          ))}
        </ul>

        <div
          className="mt-5 rounded-xl border p-5"
          style={{
            borderColor: proceed ? 'rgb(45 212 191 / 0.35)' : 'rgb(240 101 79 / 0.35)',
            background: proceed ? 'rgb(45 212 191 / 0.06)' : 'rgb(240 101 79 / 0.06)',
          }}
        >
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
            <span
              className="text-[19px] font-semibold tracking-[0.02em]"
              style={{ color: proceed ? 'var(--teal)' : 'var(--coral)' }}
            >
              {proceed ? 'PROCEED' : 'DO NOT PROCEED'}
            </span>
            <span className="text-[11.5px] text-[var(--text-muted)]">
              {FEASIBILITY_LABEL[impact.feasibility]}
            </span>
          </div>

          <ul className="mt-3.5 flex flex-col gap-2">
            {impact.rationale.map((r) => (
              <li
                key={r}
                className="flex gap-2.5 text-[12.5px] leading-relaxed text-[var(--text-primary)]"
              >
                <span
                  aria-hidden
                  className="mt-[7px] block h-1 w-1 shrink-0 rounded-full"
                  style={{ background: 'var(--border-strong)' }}
                />
                <span>{r}</span>
              </li>
            ))}
          </ul>

          <p
            className="mt-4 border-t pt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]"
            style={{ borderColor: 'var(--border)' }}
          >
            A recommendation for a human to review, not a decision. Nobody is moved until{' '}
            {candidate.employee} is asked and agrees.
          </p>
        </div>
      </GlassCard>
    </motion.div>
  );
}

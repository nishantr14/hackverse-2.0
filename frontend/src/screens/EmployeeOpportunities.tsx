import { motion } from 'framer-motion';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ErrorPanel, LoadingPanel } from '../components/Feedback';
import { GlassCard } from '../components/GlassCard';
import { IconCheck } from '../components/Icons';
import { ScreenHeader } from '../components/ScreenHeader';
import { getMyProfile, getOpenings } from '../data/api';
import type { Opening, WorkforceProfile } from '../data/types';
import { stagger } from '../lib/motion';
import { matchOpportunities, OPPORTUNITY_THRESHOLD, type Opportunity } from '../lib/opportunities';
import { useAsync } from '../lib/useAsync';
import { SHIFT_LABEL, WEEKDAY_LABEL } from '../lib/workforce';

/**
 * What the employee is shown about openings.
 *
 * A much smaller surface than the director's, and the difference is the point.
 * There is no cost here, no ranking against colleagues, no suitability score
 * relative to anybody else — an employee sees openings, why each one surfaced
 * from what THEY said, and honestly what does not line up.
 *
 * Gaps are shown as prominently as matches. A tool that only lists the reasons
 * you fit is selling you something; listing the reasons you might not is what
 * makes the fit figure worth reading at all.
 */

function Tone({ fit }: { fit: number }) {
  const strong = fit >= 75;
  const color = strong ? 'var(--teal)' : fit >= OPPORTUNITY_THRESHOLD ? 'var(--amber)' : 'var(--text-muted)';
  return (
    <div className="flex shrink-0 flex-col items-end">
      <span className="tnum text-[26px] leading-none font-semibold" style={{ color }}>
        {fit}%
      </span>
      <span className="mt-1 text-[11px] text-[var(--text-muted)]">fit to this opening</span>
    </div>
  );
}

function OpportunityCard({ opportunity }: { opportunity: Opportunity }) {
  const { opening, fit, reasons, gaps, relocationRequired } = opportunity;
  const suggested = fit >= OPPORTUNITY_THRESHOLD;

  return (
    <GlassCard className="p-5" animate={false}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
            {suggested ? 'Potential opportunity' : 'Also open'}
          </p>
          <h3 className="mt-1.5 text-[18px] font-semibold text-[var(--text-primary)]">
            {opening.project} / {opening.component}
          </h3>
          <p className="mt-1 text-[12.5px] text-[var(--text-secondary)]">
            {opening.location} · {opening.engineersRequired} engineer
            {opening.engineersRequired === 1 ? '' : 's'} needed ·{' '}
            {SHIFT_LABEL[opening.requiredShift]} shift
          </p>
        </div>
        <Tone fit={fit} />
      </div>

      {reasons.length > 0 && (
        <div className="mt-5">
          <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
            Why you may be a fit
          </p>
          <ul className="mt-2.5 flex flex-wrap gap-1.5">
            {reasons.map((r) => (
              <li
                key={r}
                className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[12px]"
                style={{
                  borderColor: 'rgb(45 212 191 / 0.28)',
                  background: 'rgb(45 212 191 / 0.08)',
                  color: 'var(--text-primary)',
                }}
              >
                <IconCheck />
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {gaps.length > 0 && (
        <div className="mt-4">
          <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
            What does not line up
          </p>
          <ul className="mt-2 flex flex-col gap-1">
            {gaps.map((g) => (
              <li
                key={g}
                className="flex gap-2 text-[12px] leading-relaxed text-[var(--text-secondary)]"
              >
                <span aria-hidden style={{ color: 'var(--border-strong)' }}>
                  —
                </span>
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div
        className="mt-5 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t pt-4"
        style={{ borderColor: 'var(--border)' }}
      >
        <p className="text-[12px] text-[var(--text-muted)]">
          Needs {opening.requiredAvailability.map((d) => WEEKDAY_LABEL[d]).join(', ')}.
          {relocationRequired
            ? ` Based in ${opening.location}.`
            : ' No relocation required.'}
        </p>
        <span className="text-[12px] text-[var(--text-muted)]">
          Nothing is applied for automatically.
        </span>
      </div>
    </GlassCard>
  );
}

export function EmployeeOpportunities() {
  const profile = useAsync<WorkforceProfile>(getMyProfile, []);
  const openings = useAsync<Opening[]>(getOpenings, []);

  const ready = profile.status === 'ready' && openings.status === 'ready';
  const opportunities = useMemo(
    () =>
      profile.status === 'ready' && openings.status === 'ready'
        ? matchOpportunities(profile.data, openings.data)
        : [],
    [profile, openings],
  );

  const error =
    profile.status === 'error' ? profile.error : openings.status === 'error' ? openings.error : null;

  const suggested = opportunities.filter((o) => o.fit >= OPPORTUNITY_THRESHOLD);
  const rest = opportunities.filter((o) => o.fit < OPPORTUNITY_THRESHOLD);

  return (
    <>
      <ScreenHeader
        eyebrow="Opportunities"
        title="Openings you may be a fit for"
        lede="Matched against the profile you filled in, and nothing else. You are never compared to another person."
      />

      <div className="mx-auto max-w-[1000px] px-6 pt-6 pb-14 sm:px-10">
        {error ? (
          <ErrorPanel error={error} />
        ) : !ready ? (
          <LoadingPanel label="Finding openings that match your profile" />
        ) : (
          <motion.div
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-6"
          >
            <div
              className="rounded-xl border px-5 py-4"
              style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.5)' }}
            >
              <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                <span className="font-semibold text-[var(--text-primary)]">
                  These come from what you told us.
                </span>{' '}
                The fit figure is a stated rule over your own profile — skills, location,
                shift and availability — not a model’s opinion of you and not a comparison with
                your colleagues. Change your{' '}
                <Link to="/me/profile" className="underline underline-offset-2">
                  profile
                </Link>{' '}
                and these change with it.
              </p>
            </div>

            {/* Empty state that says what to do about it, not just that it is empty. */}
            {opportunities.length === 0 ? (
              <GlassCard className="p-8 text-center" animate={false}>
                <p className="text-[14px] font-semibold text-[var(--text-primary)]">
                  No openings are listed right now.
                </p>
                <p className="mx-auto mt-2 max-w-md text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                  When a team publishes one, it will be matched against your profile and appear
                  here.
                </p>
              </GlassCard>
            ) : (
              <>
                {suggested.length === 0 && (
                  <GlassCard className="p-6" animate={false}>
                    <p className="text-[13px] font-semibold text-[var(--text-primary)]">
                      Nothing is a strong match yet.
                    </p>
                    <p className="mt-2 max-w-2xl text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                      The openings below are shown in full anyway, with the reasons they do not
                      line up, so you can see what is actually being asked for rather than
                      guessing. Adding skills or widening your locations on your profile will
                      change this.
                    </p>
                  </GlassCard>
                )}

                {suggested.map((o) => (
                  <OpportunityCard key={o.opening.openingId} opportunity={o} />
                ))}

                {rest.length > 0 && (
                  <>
                    <p className="text-[12px] text-[var(--text-muted)]">
                      Below the suggestion threshold — shown so you can see everything that is
                      open, not only what an algorithm picked for you.
                    </p>
                    {rest.map((o) => (
                      <OpportunityCard key={o.opening.openingId} opportunity={o} />
                    ))}
                  </>
                )}
              </>
            )}

            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              Nothing here assigns you to anything, and no manager is told you looked. A move is
              only ever proposed to you, and you can decline it.
            </p>
          </motion.div>
        )}
      </div>
    </>
  );
}

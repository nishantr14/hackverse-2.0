import { AnimatePresence, motion } from 'framer-motion';
import { useCallback, useState } from 'react';
import { Calculating } from '../components/Calculating';
import { ChoiceChips, MultiChoiceChips } from '../components/ChoiceChips';
import { ErrorPanel, Skeleton } from '../components/Feedback';
import { GlassCard } from '../components/GlassCard';
import { ProjectedImpactPanel } from '../components/ProjectedImpact';
import { RecommendationCard } from '../components/RecommendationCard';
import { ScreenHeader } from '../components/ScreenHeader';
import {
  getProjectedImpact,
  getRecommendations,
  getWorkforceRequirement,
  saveEmployeePreferences,
} from '../data/api';
import type {
  EmployeePreferences,
  ProjectedImpact,
  Recommendation,
  Shift,
  Weekday,
  WorkArea,
  WorkStyle,
  WorkforceRequirement,
} from '../data/types';
import { EASE_GLASS } from '../lib/motion';
import { useAsync } from '../lib/useAsync';
import {
  SHIFT_LABEL,
  SHIFT_OPTIONS,
  WEEKDAY_LABEL,
  WEEKDAY_OPTIONS,
  WORK_AREA_OPTIONS,
  WORK_STYLE_OPTIONS,
} from '../lib/workforce';

/**
 * Workforce fit.
 *
 * The one screen in this product that names people, and the only one built on
 * data the person volunteered rather than data we observed. That distinction is
 * stated on the screen, not just in a comment: the analytics layer is
 * pseudonymised and cannot be attributed to anyone, and if this screen ever
 * looked like an extension of it, the product's privacy claim would be false.
 *
 * The flow is one column, top to bottom, because it is a sequence rather than a
 * dashboard: what the employee told us -> what the opening needs -> who fits and
 * on what evidence -> what the simulator projects if we act on it.
 *
 * Nothing here assigns anybody. The last control says "Review", and it is
 * deliberately not a "Confirm".
 */

/**
 * Stands in for the signed-in employee until there is any notion of a session.
 * Hard-coded on purpose and in one place: the moment auth exists this becomes
 * the only line that changes.
 */
const EMPLOYEE_ID = 'employee-a';

const DEFAULT_PREFS: EmployeePreferences = {
  employeeId: EMPLOYEE_ID,
  preferredShift: 'flexible',
  workAreas: [],
  availability: [],
  workStyle: 'mixed',
  openToOtherTeams: true,
};

type SaveState =
  | { phase: 'idle' }
  | { phase: 'saving' }
  | { phase: 'saved'; at: string }
  | { phase: 'failed'; message: string };

type RecState =
  | { phase: 'idle' }
  | { phase: 'running' }
  | { phase: 'ready'; recommendations: Recommendation[]; impact: ProjectedImpact }
  | { phase: 'failed'; message: string };

/** Floor on the running state, same reasoning as the simulator's. */
const MIN_RUN_MS = 900;

export function Workforce() {
  const requirement = useAsync<WorkforceRequirement>(getWorkforceRequirement, []);

  const [prefs, setPrefs] = useState<EmployeePreferences>(DEFAULT_PREFS);
  const [save, setSave] = useState<SaveState>({ phase: 'idle' });
  const [rec, setRec] = useState<RecState>({ phase: 'idle' });

  /** Any edit invalidates a previous "Saved" — the badge must not outlive it. */
  function update(patch: Partial<EmployeePreferences>) {
    setPrefs((p) => ({ ...p, ...patch }));
    setSave((s) => (s.phase === 'saved' ? { phase: 'idle' } : s));
  }

  const onSave = useCallback(async () => {
    setSave({ phase: 'saving' });
    try {
      const result = await saveEmployeePreferences(prefs);
      // Trusts the response rather than assuming success — a backend that
      // rejects a submission has to be able to say so.
      setSave(
        result.saved
          ? { phase: 'saved', at: result.savedAt }
          : { phase: 'failed', message: 'The server did not accept these preferences.' },
      );
    } catch (err) {
      setSave({ phase: 'failed', message: err instanceof Error ? err.message : String(err) });
    }
  }, [prefs]);

  const onRecommend = useCallback(async () => {
    setRec({ phase: 'running' });
    const started = Date.now();
    try {
      const [recommendations, impact] = await Promise.all([
        getRecommendations(),
        getProjectedImpact(),
      ]);
      const wait = Math.max(0, MIN_RUN_MS - (Date.now() - started));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      setRec({ phase: 'ready', recommendations, impact });
    } catch (err) {
      setRec({ phase: 'failed', message: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  const req = requirement.status === 'ready' ? requirement.data : null;

  return (
    <>
      <ScreenHeader
        step="05"
        eyebrow="Workforce"
        title="Match people to work, on evidence they agreed to share"
        lede="Employees say how they want to work. Recommendations are drawn from that plus their resume, and every one shows what it was grounded in. A human reviews all of it."
      />

      <div className="mx-auto max-w-[1100px] px-6 pt-6 pb-14 sm:px-10">
        {requirement.status === 'error' ? (
          <ErrorPanel error={requirement.error} />
        ) : (
          <div className="flex flex-col gap-6">
            {/* Why this screen is allowed to name people at all. Stated up
                front rather than buried, because the rest of the product
                promises the opposite and both claims have to stay true. */}
            <div
              className="rounded-xl border px-5 py-4"
              style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.5)' }}
            >
              <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                <span className="font-semibold text-[var(--text-primary)]">
                  This screen is separate from the analytics.
                </span>{' '}
                Everything here was volunteered — a preference form and a resume the employee
                supplied. It is never joined to the event log, which stays pseudonymised and
                carries no per-person figure. Nothing on this screen assigns anybody to anything.
              </p>
            </div>

            {/* 1 — what the employee tells us */}
            <GlassCard className="p-5" animate={false}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                  How you would like to work
                </h2>
                <p className="text-[11.5px] text-[var(--text-muted)]">
                  Every field is optional. Blank means no preference, not no answer.
                </p>
              </div>

              <div className="mt-5 grid gap-x-8 gap-y-6 md:grid-cols-2">
                <ChoiceChips
                  legend="Preferred shift"
                  options={SHIFT_OPTIONS}
                  value={prefs.preferredShift}
                  onChange={(v: Shift) => update({ preferredShift: v })}
                />
                <MultiChoiceChips
                  legend="Interested work areas"
                  hint="pick any"
                  options={WORK_AREA_OPTIONS}
                  value={prefs.workAreas}
                  onChange={(v: WorkArea[]) => update({ workAreas: v })}
                />
                <div className="md:col-span-2">
                  <MultiChoiceChips
                    legend="Availability"
                    hint="pick any"
                    options={WEEKDAY_OPTIONS}
                    value={prefs.availability}
                    onChange={(v: Weekday[]) => update({ availability: v })}
                  />
                </div>
                <ChoiceChips
                  legend="You work best"
                  options={WORK_STYLE_OPTIONS}
                  value={prefs.workStyle}
                  onChange={(v: WorkStyle) => update({ workStyle: v })}
                />
                <ChoiceChips
                  legend="Open to other teams?"
                  options={
                    [
                      { value: 'yes', label: 'Yes' },
                      { value: 'no', label: 'No' },
                    ] as const
                  }
                  value={prefs.openToOtherTeams ? 'yes' : 'no'}
                  onChange={(v) => update({ openToOtherTeams: v === 'yes' })}
                />
              </div>

              <div
                className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-4"
                style={{ borderColor: 'var(--border)' }}
              >
                <button
                  type="button"
                  onClick={() => void onSave()}
                  disabled={save.phase === 'saving'}
                  className="h-9 rounded-lg border px-5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  style={{
                    color: 'var(--bg-page)',
                    background: 'var(--ui)',
                    borderColor: 'var(--ui)',
                  }}
                >
                  {save.phase === 'saving' ? 'Saving…' : 'Save preferences'}
                </button>

                {save.phase === 'saved' && (
                  <span className="text-[12px]" style={{ color: 'var(--teal)' }}>
                    Saved at {new Date(save.at).toLocaleTimeString()}. You can change these at any
                    time.
                  </span>
                )}
                {save.phase === 'failed' && (
                  <span className="text-[12px]" style={{ color: 'var(--coral)' }}>
                    {save.message}
                  </span>
                )}
                {save.phase === 'idle' && (
                  <span className="text-[12px] text-[var(--text-muted)]">
                    Stored against your employee record, not the event log.
                  </span>
                )}
              </div>
            </GlassCard>

            {/* 2 — the opening */}
            <GlassCard className="p-5" animate={false}>
              <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                The opening being staffed
              </h2>

              {req ? (
                <>
                  <div className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="text-[20px] font-semibold text-[var(--text-primary)]">
                      {req.project} / {req.component}
                    </span>
                    <span className="tnum text-[12.5px] text-[var(--text-secondary)]">
                      {req.engineersRequired} engineer
                      {req.engineersRequired === 1 ? '' : 's'} required
                    </span>
                  </div>

                  <dl className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-3">
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required skills
                      </dt>
                      <dd className="mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {req.requiredSkills.join(', ')}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required shift
                      </dt>
                      <dd className="mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {SHIFT_LABEL[req.requiredShift]}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required availability
                      </dt>
                      <dd className="mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {req.requiredAvailability.map((d) => WEEKDAY_LABEL[d]).join(', ')}
                      </dd>
                    </div>
                  </dl>

                  <div
                    className="mt-5 border-t pt-4"
                    style={{ borderColor: 'var(--border)' }}
                  >
                    <button
                      type="button"
                      onClick={() => void onRecommend()}
                      disabled={rec.phase === 'running'}
                      className="h-9 rounded-lg border px-5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        color: 'var(--bg-page)',
                        background: 'var(--ui)',
                        borderColor: 'var(--ui)',
                      }}
                    >
                      {rec.phase === 'running' ? 'Retrieving…' : 'Get recommendations'}
                    </button>
                  </div>
                </>
              ) : (
                <div className="mt-4 flex flex-col gap-3">
                  <Skeleton className="h-7 w-64" />
                  <Skeleton className="h-16" />
                </div>
              )}
            </GlassCard>

            {/* 3 — the retrieval, seen to happen */}
            <AnimatePresence>
              {rec.phase === 'running' && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.35, ease: EASE_GLASS }}
                  style={{ overflow: 'hidden' }}
                >
                  <GlassCard className="p-6" animate={false}>
                    <Calculating />
                  </GlassCard>
                </motion.div>
              )}
            </AnimatePresence>

            {rec.phase === 'failed' && (
              <ErrorPanel error={new Error(rec.message)} />
            )}

            {/* 4 — who fits, and on what evidence */}
            {rec.phase === 'ready' && (
              <>
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                    <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                      Recommended
                    </h2>
                    <p className="text-[11.5px] text-[var(--text-muted)]">
                      Ranked by fit to this opening. Open any card to see what it was grounded in.
                    </p>
                  </div>
                  {rec.recommendations.map((r, i) => (
                    <RecommendationCard key={r.employee} rec={r} rank={i + 1} />
                  ))}
                </div>

                <ProjectedImpactPanel impact={rec.impact} />

                <div
                  className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t pt-4"
                  style={{ borderColor: 'var(--border)' }}
                >
                  <p className="max-w-xl text-[12px] leading-relaxed text-[var(--text-primary)]">
                    A recommendation, not a decision. Nobody is moved until a human reviews this and
                    the employee agrees.
                  </p>
                  <button
                    type="button"
                    className="h-9 shrink-0 rounded-lg border px-4 text-[13px] transition-colors"
                    style={{ color: 'var(--text-secondary)', borderColor: 'var(--border)' }}
                  >
                    Review recommendation
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}

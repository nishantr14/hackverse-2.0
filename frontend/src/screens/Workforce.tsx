import { AnimatePresence, motion } from 'framer-motion';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Calculating, RECOMMENDATION_STEPS } from '../components/Calculating';
import { CandidateCard } from '../components/CandidateCard';
import { CandidateCompare } from '../components/CandidateCompare';
import { ErrorPanel, LoadingPanel } from '../components/Feedback';
import { GlassCard } from '../components/GlassCard';
import { ReallocationResult } from '../components/ReallocationResult';
import { ScenarioAssumptions } from '../components/ScenarioAssumptions';
import { ProjectPicker } from '../components/ProjectPicker';
import { ScreenHeader } from '../components/ScreenHeader';
import { Segmented } from '../components/Segmented';
import {
  getComponentDetail,
  getOpenings,
  getRecommendations,
  getSpendSummary,
  simulateReallocation,
  type CandidateSet,
  type ComponentDetail,
  type SpendSummary,
} from '../data/api';
import type {
  Opening,
  RelocationAssumptions,
  Shift,
  SimulatorOutput,
  Weekday,
} from '../data/types';
import { EASE_GLASS, stagger } from '../lib/motion';
import {
  DEFAULT_ASSUMPTIONS,
  netImpact,
  systemOutcomes,
  type NetImpact,
} from '../lib/reallocation';
import { useAsync } from '../lib/useAsync';
import { SHIFT_LABEL, WEEKDAY_LABEL } from '../lib/workforce';

/**
 * Workforce recommendations — the director's staffing surface.
 *
 * The one screen in this product that names people, and the only one built on
 * data the person volunteered rather than data we observed. That distinction is
 * stated on the screen, not just in a comment: the analytics layer is
 * pseudonymised and cannot be attributed to anyone, and if this screen ever
 * looked like an extension of it, the product's privacy claim would be false.
 *
 * WHAT IS REAL HERE AND WHAT IS NOT, because the screen mixes both on purpose:
 *   - Candidates, resumes, preferences, locations  -> fixture (volunteered layer)
 *   - Component headcount and delivery rates       -> observed, from the event log
 *   - The reallocation itself                      -> REAL, live POST /simulate
 *   - Relocation, housing, travel, extra ramp-up   -> typed-in assumptions
 *
 * The reallocation can use the real simulator without breaking the boundary
 * because it asks a question about COMPONENTS, not about people: the source key
 * is where the candidate said they work, and no identity crosses into the
 * request. Every figure carries its provenance on screen.
 *
 * The flow is one column, top to bottom, because it is a sequence rather than a
 * dashboard: which opening -> who fits and on what evidence -> compare them ->
 * what the move would cost once both projects are priced -> proceed or not.
 *
 * Nothing here assigns anybody.
 */

type RecState =
  | { phase: 'idle' }
  | { phase: 'running' }
  | { phase: 'ready'; set: CandidateSet }
  | { phase: 'failed'; message: string };

type SimState =
  | { phase: 'idle' }
  | { phase: 'running' }
  | { phase: 'ready'; sim: SimulatorOutput; impact: NetImpact }
  | { phase: 'failed'; message: string };

/** Floor on the running state, same reasoning as the simulator's. */
const MIN_RUN_MS = 900;

/**
 * The opening being staffed, as the director can edit it.
 *
 * `requiredSkills` is FREE TEXT and deliberately starts empty, including when
 * a preset fills the form. Blank means the backend derives the skills from the
 * component, which is what this screen has always done and what the basis line
 * under the list describes; the fixture openings carry their own skill list
 * that disagrees with the derived one, so copying it into the form would
 * silently change what a preset ranks on. Type into the field and the typed
 * list is used instead — and the basis line says so rather than looking the
 * same.
 */
interface OpeningForm {
  simulateKey: string;
  engineersRequired: number;
  requiredShift: Shift;
  requiredAvailability: Weekday[];
  requiredSkills: string;
  /**
   * Not one of the ranking inputs and never sent to `/workforce/recommend`.
   * It exists because the reallocation half of this screen already turns on
   * it: `feasibility()` compares it against where a candidate said they live,
   * and an opening with no location would put every candidate through a
   * relocation they may not have opted into.
   */
  location: string;
}

const WEEKDAYS: Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri'];
/** Same order and default as the Simulator's named mode. */
const SHIFTS: Shift[] = ['flexible', 'morning', 'afternoon', 'evening'];
const HEADCOUNTS = [1, 2, 3, 5, 8];

function formFromPreset(o: Opening): OpeningForm {
  return {
    simulateKey: o.simulateKey,
    engineersRequired: o.engineersRequired,
    requiredShift: o.requiredShift,
    requiredAvailability: [...o.requiredAvailability],
    requiredSkills: '',
    location: o.location,
  };
}

/** "Java, Scala" -> ["Java", "Scala"]. Blank stays blank, which means derive. */
function parseSkills(text: string): string[] {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function openingFromForm(f: OpeningForm): Opening {
  const [repo, org, component] = [
    f.simulateKey.split('/')[0] ?? '',
    f.simulateKey.split('/')[1] ?? '',
    f.simulateKey.split('/').slice(2).join('/'),
  ];
  return {
    openingId: 'custom',
    project: `${repo}/${org}`,
    component,
    simulateKey: f.simulateKey,
    location: f.location,
    engineersRequired: f.engineersRequired,
    // What the FORM says. The engine answers with what it actually ranked on
    // and the screen prints that instead — see the requirement card below.
    requiredSkills: parseSkills(f.requiredSkills),
    requiredShift: f.requiredShift,
    requiredAvailability: f.requiredAvailability,
  };
}

function sameForm(a: OpeningForm, b: OpeningForm): boolean {
  return (
    a.simulateKey === b.simulateKey &&
    a.engineersRequired === b.engineersRequired &&
    a.requiredShift === b.requiredShift &&
    a.requiredSkills.trim() === b.requiredSkills.trim() &&
    a.location === b.location &&
    a.requiredAvailability.join() === b.requiredAvailability.join()
  );
}

export function Workforce() {
  const openings = useAsync<Opening[]>(getOpenings, []);
  const components = useAsync<ComponentDetail[]>(getComponentDetail, []);
  const spend = useAsync<SpendSummary>(getSpendSummary, []);

  const [presetId, setPresetId] = useState<string | null>(null);
  const [form, setForm] = useState<OpeningForm | null>(null);
  const [rec, setRec] = useState<RecState>({ phase: 'idle' });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [comparedIds, setComparedIds] = useState<string[]>([]);
  const [assumptions, setAssumptions] = useState<RelocationAssumptions>(DEFAULT_ASSUMPTIONS);
  const [sim, setSim] = useState<SimState>({ phase: 'idle' });

  // Seeded from the first published opening rather than a constant, so an
  // empty list is an empty state instead of a crash on [0].
  useEffect(() => {
    if (openings.status === 'ready' && form === null && openings.data.length > 0) {
      const first = openings.data[0]!;
      setPresetId(first.openingId);
      setForm(formFromPreset(first));
    }
  }, [openings, form]);

  /**
   * The opening being staffed, whether it came from a preset or was typed.
   *
   * ONE SHAPE EITHER WAY. `getRecommendations` takes an `Opening`, so a custom
   * one is built into the same type rather than given a second request path —
   * the scoring, the consent gate and the privacy behaviour cannot diverge
   * between a preset and a custom opening if there is only one thing to send.
   */
  const opening = useMemo<Opening | null>(
    () => (form ? openingFromForm(form) : null),
    [form],
  );

  /** The component keys the simulator actually forecasts — the same list. */
  const componentKeys = useMemo(
    () => (components.status === 'ready' ? components.data.map((c) => c.key) : []),
    [components],
  );

  /** True when the form no longer matches the preset it was filled from. */
  const edited = useMemo(() => {
    if (!form || openings.status !== 'ready') return false;
    const preset = openings.data.find((o) => o.openingId === presetId);
    return preset ? !sameForm(form, formFromPreset(preset)) : true;
  }, [form, presetId, openings]);

  const candidates = rec.phase === 'ready' ? rec.set.candidates : [];
  /** The requirement the engine derived, once it has answered. */
  const derived = rec.phase === 'ready' ? rec.set.requirement : null;
  const selected = candidates.find((c) => c.candidateId === selectedId) ?? null;
  const compared = candidates.filter((c) => comparedIds.includes(c.candidateId));

  const headcount = useMemo(() => {
    const map = new Map<string, number>();
    if (components.status === 'ready') {
      for (const c of components.data) map.set(c.key, c.engineers);
    }
    return map;
  }, [components]);

  const blendedHourly = spend.status === 'ready' ? spend.data.blendedHourly : null;

  /** Any change to the opening invalidates everything downstream of it. */
  const invalidate = useCallback(() => {
    setRec({ phase: 'idle' });
    setSelectedId(null);
    setComparedIds([]);
    setSim({ phase: 'idle' });
  }, []);

  /** The preset dropdown fills the form; it does not replace it. */
  function choosePreset(id: string) {
    const preset = openings.status === 'ready' ? openings.data.find((o) => o.openingId === id) : null;
    if (!preset) return;
    setPresetId(id);
    setForm(formFromPreset(preset));
    invalidate();
  }

  function edit<K extends keyof OpeningForm>(key: K, value: OpeningForm[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    invalidate();
  }

  const onRecommend = useCallback(async () => {
    if (!opening || !form) return;
    setRec({ phase: 'running' });
    setSelectedId(null);
    setComparedIds([]);
    setSim({ phase: 'idle' });
    const started = Date.now();
    try {
      // The opening IS the scenario: its component, headcount, shift and days
      // are what the engine ranks against. No second requirement path, and a
      // custom opening goes through this one exactly as a preset does.
      const skills = parseSkills(form.requiredSkills);
      const set = await getRecommendations(opening, skills);
      const wait = Math.max(0, MIN_RUN_MS - (Date.now() - started));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      setRec({ phase: 'ready', set });
    } catch (err) {
      setRec({ phase: 'failed', message: err instanceof Error ? err.message : String(err) });
    }
  }, [opening, form]);

  const onSimulate = useCallback(async () => {
    if (!selected || !opening) return;
    setSim({ phase: 'running' });
    const started = Date.now();
    try {
      // The real endpoint. Components in, components out — no identity.
      const result = await simulateReallocation(selected.currentComponent, opening.simulateKey, 1);
      const wait = Math.max(0, MIN_RUN_MS - (Date.now() - started));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      setSim({
        phase: 'ready',
        sim: result,
        impact: netImpact(selected, opening, result, assumptions, blendedHourly),
      });
    } catch (err) {
      setSim({ phase: 'failed', message: err instanceof Error ? err.message : String(err) });
    }
  }, [selected, opening, assumptions, blendedHourly]);

  // Editing an assumption re-prices what is already on screen instead of
  // making the director run it again — the point of the panel is watching the
  // verdict move as the numbers change.
  useEffect(() => {
    if (sim.phase !== 'ready' || !selected || !opening) return;
    const next = netImpact(selected, opening, sim.sim, assumptions, blendedHourly);
    setSim((s) => (s.phase === 'ready' ? { ...s, impact: next } : s));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assumptions, blendedHourly]);

  const error =
    openings.status === 'error'
      ? openings.error
      : components.status === 'error'
        ? components.error
        : null;

  const outcomes =
    sim.phase === 'ready' && selected && opening
      ? systemOutcomes(
          selected,
          opening,
          sim.sim,
          (components.status === 'ready' ? components.data : [])
            .map((c) => c.key)
            .filter((k) => k !== opening.simulateKey && k !== selected.currentComponent),
        )
      : [];

  return (
    <>
      <ScreenHeader
        step="05"
        eyebrow="Workforce"
        title="Workforce recommendations"
        lede="Rank candidates against one opening on evidence they agreed to share, then price the move on both projects before anyone is asked."
      />

      <div className="mx-auto max-w-[1180px] px-6 pt-6 pb-14 sm:px-10">
        {error ? (
          <ErrorPanel error={error} />
        ) : openings.status !== 'ready' ? (
          <LoadingPanel label="Loading openings" />
        ) : (
          <motion.div
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-6"
          >
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
                Candidates are ranked on a preference form and a resume — the volunteered layer,
                never joined to the event log, which stays pseudonymised and carries no per-person
                figure. No candidate on this page has a cost, an output measure or a productivity
                score anywhere in this product, and nobody is ranked against a colleague on
                anything observed. Only an employee with a submitted preference record can be
                named at all. The reallocation below is priced by the live simulator, which is
                asked about components, never about people.
              </p>
            </div>

            {/* 1 — which opening */}
            <GlassCard className="p-5" animate={false}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
                <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                  The opening being staffed
                </h2>
                {openings.data.length > 0 && (
                  <Segmented
                    label="Preset"
                    options={openings.data.map((o) => ({
                      value: o.openingId,
                      label: `${o.component} · ${o.location}`,
                    }))}
                    value={presetId ?? openings.data[0]!.openingId}
                    onChange={choosePreset}
                    layoutId="opening-pill"
                  />
                )}
              </div>

              {opening && form ? (
                <>
                  <div className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="text-[20px] font-semibold text-[var(--text-primary)]">
                      {opening.project} / {opening.component}
                    </span>
                    <span className="tnum text-[12.5px] text-[var(--text-secondary)]">
                      {opening.engineersRequired} engineer
                      {opening.engineersRequired === 1 ? '' : 's'} required · {opening.location}
                    </span>
                    {edited && (
                      <span
                        className="rounded-full border px-2 py-0.5 text-[11px]"
                        style={{
                          borderColor: 'rgb(245 166 35 / 0.4)',
                          color: 'var(--amber)',
                        }}
                      >
                        custom opening
                      </span>
                    )}
                  </div>

                  {/* ---- the form. A preset fills it; everything stays editable. ---- */}
                  <div className="mt-5 flex flex-col gap-5 border-t pt-5" style={{ borderColor: 'var(--border)' }}>
                    <ProjectPicker
                      legend="Component to staff"
                      hint="The same components the simulator forecasts, so the move can be priced afterwards."
                      projects={componentKeys}
                      value={form.simulateKey}
                      onChange={(v) => edit('simulateKey', v)}
                    />

                    <div className="flex flex-wrap items-start gap-x-10 gap-y-5">
                      <fieldset>
                        <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                          Engineers required
                        </legend>
                        <div className="flex flex-wrap gap-1.5">
                          {HEADCOUNTS.map((n) => (
                            <button
                              key={n}
                              type="button"
                              aria-pressed={n === form.engineersRequired}
                              onClick={() => edit('engineersRequired', n)}
                              className="tnum h-9 w-9 rounded-lg border text-[13px] font-semibold transition-colors"
                              style={{
                                color:
                                  n === form.engineersRequired
                                    ? 'var(--text-primary)'
                                    : 'var(--text-secondary)',
                                background:
                                  n === form.engineersRequired ? 'var(--ui-active)' : 'transparent',
                                borderColor:
                                  n === form.engineersRequired
                                    ? 'var(--ui-active-border)'
                                    : 'var(--border)',
                              }}
                            >
                              {n}
                            </button>
                          ))}
                        </div>
                      </fieldset>

                      <fieldset>
                        <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                          Required shift
                        </legend>
                        <div className="flex flex-wrap gap-1.5">
                          {SHIFTS.map((s) => (
                            <button
                              key={s}
                              type="button"
                              aria-pressed={s === form.requiredShift}
                              onClick={() => edit('requiredShift', s)}
                              className="h-9 rounded-lg border px-3 text-[12.5px] transition-colors"
                              style={{
                                color:
                                  s === form.requiredShift
                                    ? 'var(--text-primary)'
                                    : 'var(--text-secondary)',
                                background:
                                  s === form.requiredShift ? 'var(--ui-active)' : 'transparent',
                                borderColor:
                                  s === form.requiredShift
                                    ? 'var(--ui-active-border)'
                                    : 'var(--border)',
                              }}
                            >
                              {SHIFT_LABEL[s]}
                            </button>
                          ))}
                        </div>
                      </fieldset>

                      <fieldset>
                        <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                          Required availability
                        </legend>
                        <div className="flex flex-wrap gap-1.5">
                          {WEEKDAYS.map((d) => {
                            const on = form.requiredAvailability.includes(d);
                            return (
                              <button
                                key={d}
                                type="button"
                                aria-pressed={on}
                                onClick={() =>
                                  edit(
                                    'requiredAvailability',
                                    // Never empty: an opening that needs nobody on
                                    // any day scores everyone identically, which
                                    // reads as a broken ranking rather than an
                                    // empty requirement.
                                    on
                                      ? form.requiredAvailability.filter((x) => x !== d)
                                      : WEEKDAYS.filter(
                                          (x) => x === d || form.requiredAvailability.includes(x),
                                        ),
                                  )
                                }
                                disabled={on && form.requiredAvailability.length === 1}
                                className="h-9 rounded-lg border px-3 text-[12.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                                style={{
                                  color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
                                  background: on ? 'var(--ui-active)' : 'transparent',
                                  borderColor: on ? 'var(--ui-active-border)' : 'var(--border)',
                                }}
                              >
                                {WEEKDAY_LABEL[d].slice(0, 3)}
                              </button>
                            );
                          })}
                        </div>
                      </fieldset>

                      <fieldset className="min-w-[13rem]">
                        <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                          Location
                        </legend>
                        <input
                          value={form.location}
                          onChange={(e) => edit('location', e.target.value)}
                          className="h-9 w-full rounded-lg border bg-transparent px-3 text-[12.5px] text-[var(--text-primary)]"
                          style={{ borderColor: 'var(--border)' }}
                        />
                        <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                          Not a ranking input. Used only to work out whether the move needs a
                          relocation.
                        </p>
                      </fieldset>
                    </div>

                    <fieldset>
                      <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required skills — optional
                      </legend>
                      <input
                        value={form.requiredSkills}
                        onChange={(e) => edit('requiredSkills', e.target.value)}
                        placeholder="Leave blank to derive them from the component"
                        className="h-9 w-full max-w-xl rounded-lg border bg-transparent px-3 text-[12.5px] text-[var(--text-primary)]"
                        style={{ borderColor: 'var(--border)' }}
                      />
                      <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                        Comma separated. Blank is the default and is not a lesser option — the
                        component derives its own list, and the basis line below says which of the
                        two the ranking actually used.
                      </p>
                    </fieldset>
                  </div>

                  <dl className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-4">
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required skills
                      </dt>
                      {/* Once the engine has answered, show the requirement it
                          actually ranked against. The opening publishes its own
                          list and the engine derives one from the component; two
                          different "required skills" on one screen is a screen
                          that cannot be trusted about which one was used. */}
                      <dd className="mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {derived ? (
                          derived.requiredSkills.join(', ')
                        ) : opening.requiredSkills.length ? (
                          opening.requiredSkills.join(', ')
                        ) : (
                          <span className="text-[var(--text-muted)]">
                            Derived from the component when you run it
                          </span>
                        )}
                        {derived && (
                          <span className="mt-1 block text-[11px] text-[var(--text-muted)]">
                            {derived.thin
                              ? 'The component carries no skill signal — treat this list as weak.'
                              : derived.basis}
                          </span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required shift
                      </dt>
                      <dd className="mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {SHIFT_LABEL[opening.requiredShift]}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Required availability
                      </dt>
                      <dd className="mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {opening.requiredAvailability.map((d) => WEEKDAY_LABEL[d]).join(', ')}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                        Current headcount
                      </dt>
                      <dd className="tnum mt-1.5 text-[12.5px] text-[var(--text-primary)]">
                        {headcount.get(opening.simulateKey) ?? '—'} engineers{' '}
                        <span className="text-[11px] text-[var(--text-muted)]">observed</span>
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-5 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
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
                <p className="mt-4 text-[12.5px] text-[var(--text-muted)]">
                  No openings are published right now.
                </p>
              )}
            </GlassCard>

            {/* 2 — the retrieval, seen to happen */}
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
                    {/* Not the forecast's steps: this call reads no event log,
                        and saying it did would contradict the panel above. */}
                    <Calculating steps={RECOMMENDATION_STEPS} />
                  </GlassCard>
                </motion.div>
              )}
            </AnimatePresence>

            {rec.phase === 'failed' && <ErrorPanel error={new Error(rec.message)} />}

            {/* 3 — who fits, on what evidence */}
            {rec.phase === 'ready' && opening && (
              <>
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                    <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                      Ranked for this opening
                    </h2>
                    <p className="text-[11.5px] text-[var(--text-muted)]">
                      Open “Why?” for the reasoning. Tick two or more to compare them.
                    </p>
                  </div>

                  {/* THE SYNTHETIC LABEL, and the consent gate made countable.
                      Both come from the response rather than being written
                      here, so a screen cannot keep claiming a basis after the
                      backend's has changed. */}
                  <div
                    className="rounded-xl border px-4 py-3"
                    style={{
                      borderColor: 'rgb(245 166 35 / 0.35)',
                      background: 'rgb(245 166 35 / 0.06)',
                    }}
                  >
                    <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
                      <span className="font-semibold" style={{ color: 'var(--amber)' }}>
                        {rec.set.dataBasis.label}.
                      </span>{' '}
                      {rec.set.dataBasis.note}
                    </p>
                    <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
                      {rec.set.anonymousCapacity.count} further{' '}
                      {rec.set.anonymousCapacity.count === 1 ? 'profile' : 'profiles'} could not be
                      named here. {rec.set.anonymousCapacity.note}
                      {rec.set.excluded.length > 0 && (
                        <>
                          {' '}
                          {rec.set.excluded.length}{' '}
                          {rec.set.excluded.length === 1 ? 'person was' : 'people were'} excluded on
                          a stated boundary rather than down-ranked:{' '}
                          {rec.set.excluded.map((e) => `${e.name} — ${e.reason}`).join(' ')}
                        </>
                      )}
                    </p>
                  </div>

                  {candidates.map((c, i) => (
                    <CandidateCard
                      key={c.candidateId}
                      candidate={c}
                      opening={opening}
                      rank={i + 1}
                      selected={selectedId === c.candidateId}
                      onSelect={() => {
                        setSelectedId(c.candidateId);
                        setSim({ phase: 'idle' });
                      }}
                      compared={comparedIds.includes(c.candidateId)}
                      onToggleCompare={() =>
                        setComparedIds((ids) =>
                          ids.includes(c.candidateId)
                            ? ids.filter((x) => x !== c.candidateId)
                            : [...ids, c.candidateId],
                        )
                      }
                    />
                  ))}
                </div>

                {compared.length >= 2 && (
                  <CandidateCompare
                    candidates={compared}
                    opening={opening}
                    rampUpWeeks={assumptions.extraRampUpWeeks}
                  />
                )}

                {/* 4 — the move, priced */}
                <GlassCard className="p-5" animate={false}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                    <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                      Reallocation scenario
                    </h2>
                    <p className="text-[11.5px] text-[var(--text-muted)]">
                      {selected
                        ? `${selected.employee} → ${opening.project} / ${opening.component}`
                        : 'Select a candidate above to price a move.'}
                    </p>
                  </div>

                  <div className="mt-4">
                    <ScenarioAssumptions
                      value={assumptions}
                      onChange={setAssumptions}
                      disabled={!selected}
                    />
                  </div>

                  <div
                    className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-4"
                    style={{ borderColor: 'var(--border)' }}
                  >
                    <button
                      type="button"
                      onClick={() => void onSimulate()}
                      disabled={!selected || sim.phase === 'running'}
                      className="h-9 rounded-lg border px-5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        color: 'var(--bg-page)',
                        background: 'var(--ui)',
                        borderColor: 'var(--ui)',
                      }}
                    >
                      {sim.phase === 'running' ? 'Simulating…' : 'Simulate reallocation'}
                    </button>
                    <span className="text-[12px] text-[var(--text-muted)]">
                      {selected
                        ? 'Priced by the live simulator against the event log, then the assumptions above are added.'
                        : 'No candidate selected.'}
                    </span>
                  </div>
                </GlassCard>

                <AnimatePresence>
                  {sim.phase === 'running' && (
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

                {sim.phase === 'failed' && <ErrorPanel error={new Error(sim.message)} />}

                {sim.phase === 'ready' && selected && (
                  <ReallocationResult
                    candidate={selected}
                    opening={opening}
                    sim={sim.sim}
                    impact={sim.impact}
                    outcomes={outcomes}
                    sourceHeadcount={headcount.get(selected.currentComponent) ?? null}
                    destHeadcount={headcount.get(opening.simulateKey) ?? null}
                  />
                )}
              </>
            )}

            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              Candidates are ranked by the live recommendation service over a preference form and a
              resume. Those profiles are modelled rather than submitted — our contributors are
              pseudonymised Apache accounts who filled in no form — and the response says so on
              every request. The ranking itself is a weighted sum whose terms are shown under
              “Why?” and add up to the figure beside the name. Delivery impact, component headcount
              and the blended labour rate are computed from the event log. Relocation,
              accommodation, travel and additional ramp-up are scenario assumptions typed in above,
              and are labelled as such wherever they appear.
            </p>
          </motion.div>
        )}
      </div>
    </>
  );
}

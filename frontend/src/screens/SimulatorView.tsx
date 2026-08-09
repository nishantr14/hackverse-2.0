import { AnimatePresence, motion } from 'framer-motion';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Calculating } from '../components/Calculating';
import { ConfidenceBand } from '../components/ConfidenceBand';
import { ErrorPanel } from '../components/Feedback';
import { GlassCard } from '../components/GlassCard';
import { Figure, Headline, Name } from '../components/Headline';
import { ImpactPanel } from '../components/ImpactPanel';
import { IconDownload } from '../components/Icons';
import { ScreenHeader } from '../components/ScreenHeader';
import { getSimulatorProjects, getSpend, runScenario } from '../data/api';
import type { SimulatorInput, SimulatorOutput, SpendRow } from '../data/types';
import { formatMoney, formatMoneyDelta, formatWeekDelta } from '../lib/format';
import { EASE_GLASS, snap } from '../lib/motion';
import { buildProjectPalette, colorFor } from '../lib/projectColors';
import { confidenceShape, lanesFor, sameInput } from '../lib/simulator';
import { useAsync } from '../lib/useAsync';

/**
 * The simulator.
 *
 * Four states, and the calculating one is never skipped — the spec is explicit
 * that the forecast must be seen to be computed, not looked up. The centrepiece
 * is two panels, one per project, that flood with colour as the result lands —
 * coral for the one that slips, teal for the one that gains — while the plan
 * inside each panel visibly rewrites itself: today's date slides to the
 * revised one. The net figure sits between them as the two forces' net result.
 */

interface RunResult {
  input: SimulatorInput;
  output: SimulatorOutput;
}

type State =
  | { phase: 'idle' }
  | { phase: 'calculating'; input: SimulatorInput }
  | { phase: 'result'; result: RunResult }
  | { phase: 'unavailable'; input: SimulatorInput; message: string };

/**
 * Floor on how long the calculating state is shown.
 *
 * The mock endpoint answers in 900ms, but a real one may answer in 40, and a
 * state that flashes past is worse than no state at all. The floor is applied
 * to the elapsed time, not added to it.
 */
const MIN_CALC_MS = 950;

/** Stable empty list — see the note where `projects` is derived. */
const NO_PROJECTS: string[] = [];

/**
 * The fixture assumed small product squads, where moving one person matters.
 * Real Apache components carry 60–290 contributors each, so a 1-engineer move
 * is a rounding error on the throughput. These sizes produce a delta big
 * enough to read while staying well inside what the source can spare.
 */
const COUNTS = [5, 10, 25];

export function SimulatorView() {
  const projectsAsync = useAsync<string[]>(getSimulatorProjects, []);
  const spend = useAsync<SpendRow[]>(getSpend, []);

  // A shared constant, not a fresh `[]` per render: this array is a
  // dependency of the seeding effect below, and a new identity every render
  // would re-run it every render.
  const projects = projectsAsync.status === 'ready' ? projectsAsync.data : NO_PROJECTS;
  const spendRows = spend.status === 'ready' ? spend.data : null;
  const palette = useMemo(
    () => (spendRows ? buildProjectPalette(spendRows) : new Map()),
    [spendRows],
  );

  // The component list is fetched, so there is no scenario to point at on the
  // first render. Empty strings until it lands, then seeded below — the old
  // code read available[0] from a hard-coded fixture list, which is undefined
  // now that the backend forecasts any pair it has observed delivery for.
  const [source, setSource] = useState('');
  const [dest, setDest] = useState('');
  const [count, setCount] = useState(5);
  const [state, setState] = useState<State>({ phase: 'idle' });

  useEffect(() => {
    if (projects.length >= 2) {
      setSource((s) => (s && projects.includes(s) ? s : projects[0]));
      setDest((d) => (d && projects.includes(d) ? d : projects[1]));
    }
  }, [projects]);

  /** A few real openers, built from the components that actually carry spend. */
  const available = useMemo<SimulatorInput[]>(() => {
    if (projects.length < 2) return [];
    const pairs: SimulatorInput[] = [
      { sourceProject: projects[0], destProject: projects[1], engineerCount: 5 },
      { sourceProject: projects[1], destProject: projects[0], engineerCount: 5 },
    ];
    if (projects.length >= 3) {
      pairs.push({ sourceProject: projects[0], destProject: projects[2], engineerCount: 12 });
    }
    return pairs;
  }, [projects]);

  const input: SimulatorInput = { sourceProject: source, destProject: dest, engineerCount: count };
  const sameProject = source === dest;
  // The backend forecasts any pair with observed delivery on both sides and
  // returns a 422 with a reason when it cannot, so the UI no longer needs to
  // guess in advance which combinations exist.
  const hasForecast = !sameProject && source !== '' && dest !== '';

  const run = useCallback(async (next: SimulatorInput) => {
    setState({ phase: 'calculating', input: next });
    const started = Date.now();
    try {
      const output = await runScenario(next);
      const wait = Math.max(0, MIN_CALC_MS - (Date.now() - started));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      setState({ phase: 'result', result: { input: next, output } });
    } catch (err) {
      const wait = Math.max(0, MIN_CALC_MS - (Date.now() - started));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      setState({
        phase: 'unavailable',
        input: next,
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  function applyPreset(p: SimulatorInput) {
    setSource(p.sourceProject);
    setDest(p.destProject);
    setCount(p.engineerCount);
    void run(p);
  }

  const headline = buildHeadline();

  function buildHeadline() {
    if (state.phase === 'result') {
      const { output: o, input: i } = state.result;
      const costs = o.netCostRupees > 0;
      return {
        id: `result:${i.sourceProject}-${i.destProject}-${i.engineerCount}`,
        body: (
          <>
            Moving <Figure>{i.engineerCount}</Figure> engineer
            {i.engineerCount === 1 ? '' : 's'} to{' '}
            <Name color={colorFor(palette, i.destProject).base}>{i.destProject}</Name>{' '}
            {costs ? 'costs' : 'saves'}{' '}
            <Figure color={costs ? 'var(--coral)' : 'var(--teal)'}>
              {formatMoney(Math.abs(o.netCostRupees))}
            </Figure>{' '}
            once you price what{' '}
            <Name color={colorFor(palette, i.sourceProject).base}>{i.sourceProject}</Name> loses.
          </>
        ),
        sub: `${i.sourceProject} ${formatWeekDelta(o.sourceDeltaWeeks).toLowerCase()}, ${
          i.destProject
        } ${formatWeekDelta(o.destDeltaWeeks).toLowerCase()}. The gain is real — it is just smaller than the loss.`,
      };
    }

    if (state.phase === 'calculating') {
      return {
        id: 'calculating',
        body: <>Pricing both halves of the move…</>,
        sub: 'The project losing people is being forecast too. That half is what turns an obvious win into a decision.',
      };
    }

    if (state.phase === 'unavailable') {
      return {
        id: 'unavailable',
        body: <>No forecast for this combination yet.</>,
        sub: 'The forecaster only answers where the event log carries enough evidence. Pick one of the modelled moves below.',
      };
    }

    return {
      id: 'idle',
      body: (
        <>
          Every reallocation is a trade. This one prices{' '}
          <Name color="var(--coral)">both sides</Name> of it.
        </>
      ),
      sub: 'Choose a move and run it. Watch both projects fill with the consequence — one with a delay, the other with a gain.',
    };
  }

  const error =
    projectsAsync.status === 'error'
      ? projectsAsync.error
      : spend.status === 'error'
        ? spend.error
        : null;

  const result = state.phase === 'result' ? state.result : null;
  const conf = result ? confidenceShape(result.output) : null;
  const rgb = result && result.output.netCostRupees > 0 ? '240 101 79' : '45 212 191';
  /** Idle/calculating show the current picker at rest — no delta yet. */
  const lanes = result
    ? lanesFor(result.input, result.output)
    : [
        { project: source, deltaWeeks: 0, role: 'source' as const },
        { project: dest, deltaWeeks: 0, role: 'destination' as const },
      ];

  return (
    <>
      <ScreenHeader
        step="04"
        eyebrow="Simulator"
        title="Test a reallocation before it happens"
        lede="Move engineers between projects and see the cost and delivery impact on both — including the project losing people, which is the half nobody models."
        headline={(compact) => (
          <Headline id={headline.id} sub={headline.sub} compact={compact}>
            {headline.body}
          </Headline>
        )}
      />

      <div className="mx-auto max-w-[1500px] px-10 pt-6 pb-14">
        {error ? (
          <ErrorPanel error={error} />
        ) : (
          <div className="flex flex-col gap-6">
            {/* controls — compact on purpose: the stage below is the point */}
            <GlassCard className="p-5" animate={false}>
              <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
                <ProjectPicker
                  legend="Move engineers out of"
                  projects={projects}
                  value={source}
                  onChange={setSource}
                  palette={palette}
                />
                <ProjectPicker
                  legend="Into"
                  projects={projects}
                  value={dest}
                  onChange={setDest}
                  palette={palette}
                  invalid={sameProject ? dest : null}
                />

                <div className="flex flex-col gap-2">
                  <span className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                    How many
                  </span>
                  <div className="flex gap-1.5">
                    {COUNTS.map((n) => {
                      const active = n === count;
                      return (
                        <button
                          key={n}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setCount(n)}
                          className="tnum h-9 w-9 rounded-lg border text-[13px] font-semibold transition-colors"
                          style={{
                            color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                            background: active ? 'var(--ui-active)' : 'transparent',
                            borderColor: active ? 'var(--ui-active-border)' : 'var(--border)',
                          }}
                        >
                          {n}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="ml-auto flex items-center gap-3">
                  <button
                    type="button"
                    disabled={sameProject || state.phase === 'calculating'}
                    onClick={() => void run(input)}
                    className="h-9 rounded-lg border px-5 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                    style={{ color: 'var(--bg-page)', background: 'var(--ui)', borderColor: 'var(--ui)' }}
                  >
                    {state.phase === 'calculating' ? 'Running…' : 'Run scenario'}
                  </button>
                  {state.phase === 'result' && (
                    <button
                      type="button"
                      // Loaded on demand — jsPDF pulls in html2canvas as a
                      // dependency of a method this file never calls, and
                      // there is no reason every visitor's initial bundle
                      // should carry ~270KB for an export nobody has asked
                      // for yet.
                      onClick={async () => {
                        const { exportScenarioPdf } = await import('../lib/exportPdf');
                        exportScenarioPdf({ input: state.result.input, output: state.result.output });
                      }}
                      className="flex h-9 items-center gap-1.5 rounded-lg border px-4 text-[13px] transition-colors"
                      style={{ color: 'var(--text-secondary)', borderColor: 'var(--border)' }}
                    >
                      <IconDownload />
                      Export PDF
                    </button>
                  )}
                  {state.phase !== 'idle' && state.phase !== 'calculating' && (
                    <button
                      type="button"
                      onClick={() => setState({ phase: 'idle' })}
                      className="h-9 rounded-lg border px-4 text-[13px] transition-colors"
                      style={{ color: 'var(--text-secondary)', borderColor: 'var(--border)' }}
                    >
                      Reset
                    </button>
                  )}
                </div>
              </div>

              <div
                className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 border-t pt-3.5"
                style={{ borderColor: 'var(--border)' }}
              >
                <span className="text-[11.5px] text-[var(--text-secondary)]">
                  {sameProject
                    ? 'Pick two different projects.'
                    : hasForecast
                      ? 'The event log carries evidence for this move.'
                      : 'No evidence for this exact move — the modelled ones are:'}
                </span>
                {available.map((p) => (
                  <button
                    key={`${p.sourceProject}-${p.destProject}-${p.engineerCount}`}
                    type="button"
                    onClick={() => applyPreset(p)}
                    className="rounded-full border px-3 py-1 text-[11.5px] transition-colors"
                    style={{
                      borderColor: sameInput(p, input) ? 'var(--ui-active-border)' : 'var(--border)',
                      background: sameInput(p, input) ? 'var(--ui-active)' : 'transparent',
                      color: sameInput(p, input) ? 'var(--text-primary)' : 'var(--text-secondary)',
                    }}
                  >
                    {p.engineerCount} × {p.sourceProject} → {p.destProject}
                  </button>
                ))}
              </div>
            </GlassCard>

            {/* calculating strip — shown above the stage, not instead of it, so
                the beam is always the thing on screen */}
            <AnimatePresence>
              {state.phase === 'calculating' && (
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
              {state.phase === 'unavailable' && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.35, ease: EASE_GLASS }}
                  style={{ overflow: 'hidden' }}
                >
                  <GlassCard className="p-6" animate={false}>
                    <p className="text-[14px] font-semibold text-[var(--text-primary)]">
                      The forecaster declined this one
                    </p>
                    <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-[var(--text-secondary)]">
                      {state.message} Returning a number here would mean inventing one.
                    </p>
                  </GlassCard>
                </motion.div>
              )}
            </AnimatePresence>

            {/* the stage — two panels, always mounted so they never disappear
                between states; only what fills them changes */}
            <div className="grid items-stretch gap-4 lg:grid-cols-[minmax(0,1fr)_15rem_minmax(0,1fr)]">
              <ImpactPanel
                lane={lanes[0]}
                engineerCount={input.engineerCount}
                revealed={state.phase === 'result'}
                conf={conf}
                identityColor={colorFor(palette, lanes[0].project).base}
              />

              {/* the net figure — where the two floods' consequences meet */}
              <div className="flex flex-col items-center justify-center gap-5 py-6 text-center">
                <div>
                  <p className="text-[11px] tracking-[0.1em] text-[var(--text-secondary)] uppercase">
                    Net cost impact
                  </p>
                  <p
                    className="tnum mt-1.5 text-[40px] leading-none font-semibold"
                    style={{
                      color: `rgb(${rgb})`,
                      opacity: result ? 1 : 0,
                      transform: result ? 'scale(1)' : 'scale(0.85)',
                      transition: 'opacity 0.4s cubic-bezier(0.22,1,0.36,1) 0.85s, transform 0.4s cubic-bezier(0.22,1,0.36,1) 0.85s',
                    }}
                  >
                    {result ? formatMoneyDelta(result.output.netCostRupees) : '—'}
                  </p>
                </div>

                {result && conf && (
                  <div className="w-full max-w-[15rem]">
                    <ConfidenceBand
                      low={result.output.confidenceLow}
                      high={result.output.confidenceHigh}
                      percent={result.output.confidencePercent}
                      conf={conf}
                      rgb={rgb}
                      revealed
                    />
                  </div>
                )}

                {result?.output.rampUpPenaltyApplied && (
                  <div
                    className="w-full rounded-lg border p-3"
                    style={{ borderColor: 'rgb(245 166 35 / 0.4)', background: 'rgb(245 166 35 / 0.07)' }}
                  >
                    <p
                      className="flex items-center justify-center gap-2 text-[11.5px] font-semibold"
                      style={{ color: 'var(--amber)' }}
                    >
                      <span aria-hidden>▲</span>
                      Ramp-up adjustment applied
                    </p>
                    <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-secondary)]">
                      {result.output.rampUpNote ??
                        'Limited experience in this component — adjustment applied.'}
                    </p>
                  </div>
                )}
              </div>

              <ImpactPanel
                lane={lanes[1]}
                engineerCount={input.engineerCount}
                revealed={state.phase === 'result'}
                conf={conf}
                identityColor={colorFor(palette, lanes[1].project).base}
              />
            </div>

            <div
              className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t pt-4"
              style={{ borderColor: 'var(--border)' }}
            >
              <p className="text-[12px] leading-relaxed text-[var(--text-primary)]">
                Scenarios, not decisions. A human reviews every reallocation.
              </p>
              <p className="text-[12px] text-[var(--text-secondary)]">
                All figures computed from the event log. No individual is named or scored anywhere
                in this product.
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function ProjectPicker({
  legend,
  projects,
  value,
  onChange,
  palette,
  invalid,
}: {
  legend: string;
  projects: string[];
  value: string;
  onChange: (v: string) => void;
  palette: ReturnType<typeof buildProjectPalette>;
  invalid?: string | null;
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
        {legend}
      </legend>
      <div className="flex flex-wrap gap-1.5">
        {projects.map((p) => {
          const active = p === value;
          const c = colorFor(palette, p);
          const bad = invalid === p;
          return (
            <motion.button
              key={p}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(p)}
              transition={snap}
              className="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors"
              style={{
                borderColor: bad
                  ? 'rgb(240 101 79 / 0.5)'
                  : active
                    ? `rgb(${c.rgb} / 0.7)`
                    : 'var(--border)',
                background: active ? `rgb(${c.rgb} / 0.14)` : 'transparent',
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
              }}
            >
              <span
                aria-hidden
                className="block h-2 w-2 shrink-0 rounded-full"
                style={{ background: c.base, opacity: active ? 1 : 0.5 }}
              />
              {p}
            </motion.button>
          );
        })}
      </div>
    </fieldset>
  );
}

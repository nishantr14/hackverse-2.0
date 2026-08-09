import { motion } from 'framer-motion';
import { useMemo, useState } from 'react';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { ErrorPanel, LoadingPanel } from '../components/Feedback';
import { Figure, Headline, Name } from '../components/Headline';
import { GlassCard } from '../components/GlassCard';
import { HoursSplit } from '../components/HoursSplit';
import { MetricStrip } from '../components/MetricStrip';
import { ScreenHeader } from '../components/ScreenHeader';
import { Segmented } from '../components/Segmented';
import { SpendMap } from '../components/SpendMap';
import { SpendTable } from '../components/SpendTable';
import { getSpend, getWaste } from '../data/api';
import type { SpendRow, WasteRow } from '../data/types';
import {
  formatHours,
  formatMoney,
  formatPercent,
  formatRupeesExact,
  moneyScaleFormatter,
} from '../lib/format';
import { EASE_GLASS, stagger } from '../lib/motion';
import { buildProjectPalette, colorFor } from '../lib/projectColors';
import { byComponent, byProject, flaggedComponents, totals, type ComponentSpend } from '../lib/spend';
import { useAsync } from '../lib/useAsync';

type View = 'map' | 'table';
type Grouping = 'hierarchy' | 'component';

const VIEWS = [
  { value: 'map' as const, label: 'Map' },
  { value: 'table' as const, label: 'Table' },
];

const GROUPINGS = [
  { value: 'hierarchy' as const, label: 'By project' },
  { value: 'component' as const, label: 'By component' },
];

export function SpendView() {
  const [view, setView] = useState<View>('map');
  const [grouping, setGrouping] = useState<Grouping>('hierarchy');

  /** Shared across the map and the hours chart — pointing at either focuses both. */
  const [focusProject, setFocusProject] = useState<string | null>(null);
  const [focusComponent, setFocusComponent] = useState<ComponentSpend | null>(null);

  const spend = useAsync<SpendRow[]>(getSpend, []);
  const waste = useAsync<WasteRow[]>(getWaste, []);

  const rows = spend.status === 'ready' ? spend.data : null;
  const wasteRows = waste.status === 'ready' ? waste.data : null;

  const t = useMemo(() => (rows ? totals(rows) : null), [rows]);
  const projects = useMemo(() => (rows ? byProject(rows) : []), [rows]);
  const components = useMemo(() => (rows ? byComponent(rows) : []), [rows]);
  const palette = useMemo(() => (rows ? buildProjectPalette(rows) : new Map()), [rows]);
  const flagged = useMemo(() => (wasteRows ? flaggedComponents(wasteRows) : new Map()), [wasteRows]);

  const error =
    spend.status === 'error' ? spend.error : waste.status === 'error' ? waste.error : null;
  const ready = rows !== null && wasteRows !== null && t !== null;

  const headline = ready ? buildHeadline() : null;

  function buildHeadline() {
    const total = t!.cost;
    const focusedProject = focusProject
      ? projects.find((p) => p.project === focusProject)
      : null;

    if (focusComponent) {
      const c = colorFor(palette, focusComponent.project);
      return {
        id: `component:${focusComponent.project}/${focusComponent.component}`,
        body: (
          <>
            <Name color={c.base}>{focusComponent.component}</Name> cost{' '}
            <Figure>{formatMoney(focusComponent.cost)}</Figure> — that is{' '}
            {formatPercent(focusComponent.cost / total)} of the quarter&rsquo;s engineering spend.
          </>
        ),
        sub: `${focusComponent.project} · ${focusComponent.workItems.length} work item${
          focusComponent.workItems.length === 1 ? '' : 's'
        } · ${focusComponent.authorHours} h authoring, ${focusComponent.reviewHours} h in review.`,
      };
    }

    if (focusedProject) {
      const c = colorFor(palette, focusedProject.project);
      return {
        id: `project:${focusedProject.project}`,
        body: (
          <>
            <Name color={c.base}>{focusedProject.project}</Name> took{' '}
            <Figure>{formatMoney(focusedProject.cost)}</Figure> —{' '}
            {formatPercent(focusedProject.cost / total)} of everything this quarter.
          </>
        ),
        sub: `${focusedProject.workItems} work items across ${
          focusedProject.components.length
        } component${
          focusedProject.components.length === 1 ? '' : 's'
        } · ${formatHours(focusedProject.authorHours + focusedProject.reviewHours)} engineer-hours.`,
      };
    }

    const topProject = projects[0];
    const topComponent = components[0];
    return {
      id: 'default',
      body: (
        <>
          <Name color={colorFor(palette, topProject.project).base}>{topProject.project}</Name> is
          the most expensive project this quarter — <Figure>{formatMoney(topProject.cost)}</Figure>{' '}
          of a <Figure>{formatMoney(total)}</Figure> engineering bill.
        </>
      ),
      sub: `The single most expensive component is ${topComponent.component} at ${formatMoney(
        topComponent.cost,
      )}. Point at any tile to trace where a rupee went.`,
    };
  }

  return (
    <>
      <ScreenHeader
        step="02"
        eyebrow="Spend"
        title="Where the money went"
        lede="Every work item priced from observed engineer-hours against role-band rates."
        headline={
          headline
            ? (compact) => (
                <Headline id={headline.id} sub={headline.sub} compact={compact}>
                  {headline.body}
                </Headline>
              )
            : undefined
        }
        controls={
          <>
            {view === 'map' && (
              <Segmented
                label="Group spend by"
                options={GROUPINGS}
                value={grouping}
                onChange={setGrouping}
                layoutId="grouping-pill"
              />
            )}
            <Segmented
              label="View as"
              options={VIEWS}
              value={view}
              onChange={setView}
              layoutId="view-pill"
            />
          </>
        }
      />

      <div className="mx-auto max-w-[1500px] px-10 pt-6 pb-14">
        {error ? (
          <ErrorPanel error={error} />
        ) : !ready ? (
          <LoadingPanel label="Loading spend data" />
        ) : (
          <motion.div
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-6"
          >
            <MetricStrip
              hero={{
                label: 'Total engineering spend',
                value: <AnimatedNumber value={t.cost} format={moneyScaleFormatter(t.cost)} />,
                detail: `${t.workItems} work items · ${t.projects} projects · ${formatHours(
                  t.totalHours,
                )} engineer-hours`,
                formula: (
                  <>
                    Sum of <strong>cost</strong> across every priced work item in the event log.
                    Cost per item = observed engineer-hours × role-band rate. Exact total{' '}
                    {formatRupeesExact(t.cost)}.
                  </>
                ),
              }}
              metrics={[
                {
                  label: 'Blended cost per hour',
                  value: (
                    <AnimatedNumber
                      value={Math.round(t.blendedRate)}
                      format={moneyScaleFormatter(Math.round(t.blendedRate))}
                      duration={0.9}
                    />
                  ),
                  detail: `${formatMoney(t.labourCost)} labour ÷ ${formatHours(
                    t.totalHours,
                  )}`,
                  formula: (
                    <>
                      Labour spend ÷ engineer-hours. Meetings, CI and tokens are excluded from
                      this one — they are in the total above, but none of them is an
                      engineer-hour, and dividing by them would push the rate above the staff
                      band.
                    </>
                  ),
                },
                {
                  label: 'Hours spent in review',
                  value: (
                    <AnimatedNumber
                      value={t.reviewShare * 100}
                      format={(n) => `${n.toFixed(1)}%`}
                      duration={0.9}
                    />
                  ),
                  detail: `${formatHours(t.reviewHours)} review of ${formatHours(
                    t.totalHours,
                  )} total`,
                  formula: (
                    <>
                      Review hours ÷ (author + review) hours. Screen 03 prices what the waiting
                      costs.
                    </>
                  ),
                },
              ]}
            />

            {view === 'table' ? (
              <GlassCard className="p-8">
                <motion.div
                  key="table"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: EASE_GLASS }}
                >
                  <SpendTable rows={rows} total={t.cost} />
                </motion.div>
              </GlassCard>
            ) : (
              // Side by side, not stacked: hovering a tile highlights the matching
              // project row, and that link is worthless if you have to scroll to
              // see the other half of it.
              <div className="grid gap-6 xl:grid-cols-[minmax(0,1.85fr)_minmax(0,1fr)]">
                <GlassCard className="p-7">
                  <SpendMap
                    projects={projects}
                    components={components}
                    total={t.cost}
                    flagged={flagged}
                    mode={grouping}
                    palette={palette}
                    focusProject={focusProject}
                    onFocusProject={setFocusProject}
                    onFocusComponent={setFocusComponent}
                  />
                </GlassCard>

                <GlassCard className="p-7">
                  <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
                    Where the hours went
                  </h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                    <span className="tnum text-[var(--text-primary)]">
                      {formatPercent(t.reviewShare)}
                    </span>{' '}
                    of all engineer-hours went to reviewing, not authoring.
                  </p>

                  <div className="mt-5">
                    <HoursSplit
                      projects={projects}
                      palette={palette}
                      focusProject={focusProject}
                      onFocusProject={setFocusProject}
                    />
                  </div>
                </GlassCard>
              </div>
            )}

            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              All figures computed from the event log. Rates are role-band medians from public
              compensation data — no individual salary is ever ingested, and no per-person figure is
              rendered anywhere in this product.
            </p>
          </motion.div>
        )}
      </div>
    </>
  );
}

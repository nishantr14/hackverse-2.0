import { motion } from 'framer-motion';
import { useMemo, useState } from 'react';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { ErrorPanel, LoadingPanel } from '../components/Feedback';
import { GlassCard } from '../components/GlassCard';
import { Figure, Headline, Name } from '../components/Headline';
import { MetricStrip } from '../components/MetricStrip';
import { ProcessMap } from '../components/ProcessMap';
import { ScreenHeader } from '../components/ScreenHeader';
import { VariantBars } from '../components/VariantBars';
import { getProcessGraph } from '../data/api';
import type { ProcessEdge, ProcessGraph } from '../data/types';
import { formatMoney, formatPercent } from '../lib/format';
import { stagger } from '../lib/motion';
import {
  offHappyPathCostShare,
  reworkPasses,
  totalWorkItems,
  variantStats,
  variantTone,
  VARIANT_LABEL,
} from '../lib/process';
import { useAsync } from '../lib/useAsync';

export function ProcessView() {
  const [variant, setVariant] = useState<string | null>(null);
  const [activeEdge, setActiveEdge] = useState<ProcessEdge | null>(null);

  const graph = useAsync<ProcessGraph>(getProcessGraph, []);
  const data = graph.status === 'ready' ? graph.data : null;

  const stats = useMemo(() => (data ? variantStats(data) : []), [data]);
  const offPath = data ? offHappyPathCostShare(data) : 0;
  const passes = data ? reworkPasses(data) : null;
  const items = data ? totalWorkItems(data) : 0;
  /** Sorted by costMultiple, so the first row is the path that overcharges most. */
  const worst = stats[0];

  const headline = data ? buildHeadline() : null;

  function buildHeadline() {
    if (activeEdge) {
      const from = data!.nodes.find((n) => n.id === activeEdge.from)?.label ?? activeEdge.from;
      const to = data!.nodes.find((n) => n.id === activeEdge.to)?.label ?? activeEdge.to;
      const tone = variantTone(activeEdge.variant);
      return {
        id: `edge:${activeEdge.from}-${activeEdge.to}-${activeEdge.variant}-${activeEdge.frequency}`,
        body: (
          <>
            <Name color={tone.css}>
              {from} → {to}
            </Name>{' '}
            cost <Figure>{formatMoney(activeEdge.costRupees)}</Figure> across{' '}
            <Figure>{activeEdge.frequency}</Figure> passes.
          </>
        ),
        sub: `On the ${(VARIANT_LABEL[activeEdge.variant] ?? activeEdge.variant).toLowerCase()}. That is ${formatMoney(
          Math.round(activeEdge.costRupees / activeEdge.frequency),
        )} of engineer time every time this transition fires.`,
      };
    }

    if (variant) {
      const s = stats.find((v) => v.variant === variant)!;
      const tone = variantTone(variant);
      return {
        id: `variant:${variant}`,
        body: (
          <>
            <Name color={tone.css}>{s.label}</Name> carries{' '}
            <Figure>{formatPercent(s.shareOfWorkItems, 0)}</Figure> of the work and{' '}
            <Figure>{formatPercent(s.shareOfCost, 0)}</Figure> of the cost.
          </>
        ),
        sub: `${s.costMultiple.toFixed(1)}× its weight · ${formatMoney(s.cost)} across ${
          s.cases
        } work items.`,
      };
    }

    return {
      id: 'default',
      body: (
        <>
          <Figure color={variantTone(worst.variant).css}>
            {formatPercent(worst.shareOfWorkItems, 0)}
          </Figure>{' '}
          of work items take the{' '}
          <Name color={variantTone(worst.variant).css}>{worst.label.toLowerCase()}</Name> path — and
          they burn <Figure>{formatPercent(worst.shareOfCost, 0)}</Figure> of the money.
        </>
      ),
      sub: `${items} work items went from first commit to deploy in the last 12 months. They took three different routes, and the routes did not cost the same.`,
    };
  }

  return (
    <>
      <ScreenHeader
        step="01"
        eyebrow="Process"
        title="How work actually moves"
        lede="Commit to deploy, reconstructed from the event log and weighted by what each transition costs."
        headline={
          headline
            ? (compact) => (
                <Headline id={headline.id} sub={headline.sub} compact={compact}>
                  {headline.body}
                </Headline>
              )
            : undefined
        }
      />

      <div className="mx-auto max-w-[1500px] px-10 pt-6 pb-14">
        {graph.status === 'error' ? (
          <ErrorPanel error={graph.error} />
        ) : !data || !worst ? (
          <LoadingPanel label="Loading the process graph" />
        ) : (
          <motion.div
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-6"
          >
            <MetricStrip
              hero={{
                label: 'Cost off the happy path',
                value: (
                  <AnimatedNumber value={offPath * 100} format={(n) => `${n.toFixed(0)}%`} />
                ),
                detail: 'Share of engineering cost spent on rework and repeat review',
                formula: (
                  <>
                    Sum of <strong>shareOfCost</strong> for every variant that is not the happy
                    path. The remaining {formatPercent(1 - offPath, 0)} is work that went commit →
                    review → merge → deploy without turning back.
                  </>
                ),
              }}
              metrics={[
                {
                  label: `${worst.label} multiple`,
                  value: (
                    <AnimatedNumber
                      value={worst.costMultiple}
                      format={(n) => `${n.toFixed(1)}×`}
                      duration={0.9}
                    />
                  ),
                  detail: `${formatPercent(worst.shareOfCost, 0)} of cost on ${formatPercent(
                    worst.shareOfWorkItems,
                    0,
                  )} of work items`,
                  formula: (
                    <>
                      Share of cost ÷ share of work items. Above 1× means the path charges more
                      than its weight — this is the number that says where a process fix pays.
                    </>
                  ),
                },
                {
                  label: 'Returns to review',
                  value:
                    passes === null ? (
                      <span className="text-[var(--text-muted)]">—</span>
                    ) : (
                      <AnimatedNumber
                        value={passes}
                        format={(n) => `${Math.round(n)}`}
                        duration={0.9}
                      />
                    ),
                  detail:
                    passes === null
                      ? 'Not reported by this backend'
                      : `Times finished work was sent back for changes, across ${
                          data.reworkReturns?.cases ?? 0
                        } work items`,
                  formula: (
                    <>
                      Count of every <strong>changes requested</strong> event in the log. Measured
                      directly, not counted off the map above — the map is filtered to the costliest
                      transitions so it stays legible, and the returns to review sit below that cut.
                      The {data.reworkReturns?.cases ?? 0} work items behind it are the same{' '}
                      {data.reworkReturns?.cases ?? 0} on the rework loop.
                    </>
                  ),
                },
              ]}
            />

            {/* the diagram — the hero of the screen. Full width, drawn large,
                the actual shape of the process rather than a description of
                it. Point at a line to price that transition; pick a route
                below to isolate it here. */}
            <GlassCard className="p-8">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">
                  Commit to deploy, drawn by what it costs
                </h2>
                <p className="text-[12.5px] text-[var(--text-secondary)]">
                  Thicker line = more rupees on that transition. Point at any line to price it.
                </p>
              </div>
              <div className="mt-7">
                <ProcessMap
                  graph={data}
                  variant={variant}
                  activeEdge={activeEdge}
                  onActiveEdge={setActiveEdge}
                />
              </div>
              {/* Say what is off screen. A filtered map that does not admit
                  it is filtered is just a wrong map. */}
              {data.coverage && (
                <p className="mt-5 border-t pt-4 text-[12px] leading-relaxed text-[var(--text-secondary)]"
                   style={{ borderColor: 'var(--border)' }}>
                  Showing the {data.coverage.transitionsShown} costliest of{' '}
                  {data.coverage.transitionsTotal} transitions —{' '}
                  <strong className="text-[var(--text-primary)]">
                    {Math.round(data.coverage.costShare * 100)}% of all transition cost
                  </strong>
                  . {data.coverage.note}
                </p>
              )}
            </GlassCard>

            {/* supporting detail — the same three routes as numbers, so the
                diagram's shape and the underlying figures stay one click apart */}
            <GlassCard className="p-7">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
                  What each route is worth
                </h2>
                <p className="text-[12.5px] text-[var(--text-secondary)]">
                  {items} work items took one of three routes in the last 12 months.
                </p>
              </div>
              <div className="mt-5 grid gap-x-8 gap-y-5 sm:grid-cols-3">
                <VariantBars stats={stats} selected={variant} onSelect={setVariant} layout="items" />
              </div>
            </GlassCard>

            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              All figures computed from the event log. A route is a distinct ordering of activities
              for a work item; the cost of a transition is the engineer time recorded between the
              two activities, priced at role-band rates.
            </p>
          </motion.div>
        )}
      </div>
    </>
  );
}

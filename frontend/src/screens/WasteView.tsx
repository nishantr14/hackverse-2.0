import { motion } from 'framer-motion';
import { useMemo, useState } from 'react';
import { AnimatedNumber } from '../components/AnimatedNumber';
import { ErrorPanel, LoadingPanel } from '../components/Feedback';
import { GlassCard } from '../components/GlassCard';
import { Figure, Headline, Name } from '../components/Headline';
import { MetricStrip } from '../components/MetricStrip';
import { ScreenHeader } from '../components/ScreenHeader';
import { WasteCards } from '../components/WasteCards';
import { getSpend, getWaste } from '../data/api';
import type { SpendRow, WasteRow } from '../data/types';
import { formatMoney, formatPercent, moneyScaleFormatter, wasteLabel } from '../lib/format';
import { EASE_GLASS, stagger } from '../lib/motion';
import { buildProjectPalette, colorFor } from '../lib/projectColors';
import { useAsync } from '../lib/useAsync';
import {
  byProjectWaste,
  categorise,
  exposureTotal,
  largestLine,
  recoverableTotal,
  WASTE_TONE,
} from '../lib/waste';

const TONE_CSS: Record<string, string> = {
  amber: 'var(--amber)',
  coral: 'var(--coral)',
  teal: 'var(--teal)',
  neutral: 'var(--text-secondary)',
};

export function WasteView() {
  const [selected, setSelected] = useState<string | null>(null);

  const waste = useAsync<WasteRow[]>(getWaste, []);
  const spend = useAsync<SpendRow[]>(getSpend, []);

  const rows = waste.status === 'ready' ? waste.data : null;
  const spendRows = spend.status === 'ready' ? spend.data : null;

  const categories = useMemo(() => (rows ? categorise(rows) : []), [rows]);
  const projectWaste = useMemo(() => (rows ? byProjectWaste(rows) : []), [rows]);
  const palette = useMemo(
    () => (spendRows ? buildProjectPalette(spendRows) : new Map()),
    [spendRows],
  );

  const recoverable = rows ? recoverableTotal(rows) : 0;
  const exposure = rows ? exposureTotal(rows) : 0;
  const biggest = rows ? largestLine(rows) : null;

  const error = waste.status === 'error' ? waste.error : spend.status === 'error' ? spend.error : null;
  const ready = rows !== null && spendRows !== null && biggest !== null;

  /** The ledger, filtered to whichever card is selected. */
  const ledger = useMemo(() => {
    if (!rows) return [];
    const list = selected ? rows.filter((r) => r.type === selected) : rows;
    return [...list].sort((a, b) => b.amountRupees - a.amountRupees);
  }, [rows, selected]);

  const headline = ready ? buildHeadline() : null;

  function buildHeadline() {
    if (selected) {
      const c = categories.find((x) => x.type === selected)!;
      return {
        id: `category:${selected}`,
        body: (
          <>
            <Name color={TONE_CSS[c.tone]}>{c.label}</Name> accounts for{' '}
            <Figure>{formatMoney(c.amount)}</Figure>
            {c.recoverable ? (
              <>
                {' '}
                — <Figure>{formatPercent(c.share, 0)}</Figure> of everything recoverable.
              </>
            ) : (
              <> of work at risk — money that has not been lost yet.</>
            )}
          </>
        ),
        sub: c.rows.map((r) => `${r.component ?? r.project}: ${formatMoney(r.amountRupees)}`).join(' · '),
      };
    }

    // No ratio against total spend here on purpose: the waste fixture and the
    // spend fixture are not drawn to the same scale, and dividing one by the
    // other produced "₹46.4L of a ₹33.1L bill" — an impossibility, printed as
    // the headline. Each figure is reported on its own terms until the two
    // datasets reconcile.
    return {
      id: 'default',
      body: (
        <>
          <Figure color="var(--amber)">{formatMoney(recoverable)}</Figure> of engineering time in
          the last 12 months bought nothing that still exists.
        </>
      ),
      sub:
        exposure > 0
          ? `A further ${formatMoney(
              exposure,
            )} of in-flight work sits behind single owners — exposed, not yet lost. Pick a category to open its ledger.`
          : 'Key-person exposure is computed offline and never served over the API — it reads a per-actor view on purpose. Pick a category to open its ledger.',
    };
  }

  return (
    <>
      <ScreenHeader
        step="03"
        eyebrow="Waste and risk"
        title="What the money bought that lasted"
        lede="Five failure modes, each traced back to the rows in the event log that produced it. Three carry a price; review latency is reported as time, and key-person exposure never leaves the machine."
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
        {error ? (
          <ErrorPanel error={error} />
        ) : !ready ? (
          <LoadingPanel label="Loading waste and risk" />
        ) : (
          <motion.div
            variants={stagger(0.07)}
            initial="hidden"
            animate="show"
            className="flex flex-col gap-6"
          >
            <MetricStrip
              hero={{
                label: 'Recoverable waste',
                value: (
                  <AnimatedNumber value={recoverable} format={moneyScaleFormatter(recoverable)} />
                ),
                detail: `Meetings, CI reruns and rework · ${categories
                  .filter((c) => c.recoverable && c.priced)
                  .reduce((s, c) => s + c.rows.length, 0)} priced lines across ${
                  projectWaste.length
                } projects`,
                formula: (
                  <>
                    Meeting cost + CI rerun waste + rework. Review latency is{' '}
                    <strong>not</strong> in this total — waiting is wall clock, and nobody is
                    billed to wait, so it is reported as time instead. Key-person exposure is
                    excluded too: it is value at risk, not money already spent, and summing the
                    two would produce a figure that means nothing.
                  </>
                ),
              }}
              metrics={[
                {
                  label: 'Key-person exposure',
                  value:
                    exposure > 0 ? (
                      <AnimatedNumber value={exposure} format={moneyScaleFormatter(exposure)} />
                    ) : (
                      <span className="text-[19px]">Not served</span>
                    ),
                  detail:
                    exposure > 0
                      ? 'In-flight value behind single owners — at risk, not spent'
                      : 'Computed offline only — the API role cannot read it',
                  formula: (
                    <>
                      Value of open work in components where one author owns most recent changes.
                      It is per-actor by construction, so the view it needs is deliberately never
                      granted to the API role and this figure cannot be served over HTTP at any
                      aggregation. Run <code>python -m app.waste.key_person</code> to see it. That
                      refusal is the privacy design working, not a gap.
                    </>
                  ),
                },
                {
                  label: 'Largest single line',
                  value: (
                    <AnimatedNumber
                      value={biggest.amountRupees}
                      format={moneyScaleFormatter(biggest.amountRupees)}
                      duration={0.9}
                    />
                  ),
                  detail: `${wasteLabel[biggest.type] ?? biggest.type} in ${
                    biggest.component ?? biggest.project
                  }`,
                  formula: <>{biggest.detail}</>,
                },
              ]}
            />

            <WasteCards categories={categories} selected={selected} onSelect={setSelected} />

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
              <GlassCard className="p-7">
                <div className="flex flex-wrap items-baseline justify-between gap-3">
                  <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
                    {selected ? `${wasteLabel[selected] ?? selected} ledger` : 'Every priced line'}
                  </h2>
                  {selected && (
                    <button
                      type="button"
                      onClick={() => setSelected(null)}
                      className="text-[12px] text-[var(--text-secondary)] underline decoration-dotted underline-offset-4"
                    >
                      Show all categories
                    </button>
                  )}
                </div>

                <ul className="mt-5 flex flex-col gap-3">
                  {ledger.map((r, i) => {
                    const c = colorFor(palette, r.project);
                    return (
                      <motion.li
                        key={`${r.type}-${r.project}-${r.component ?? 'none'}`}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.4, ease: EASE_GLASS, delay: 0.04 * i }}
                        className="rounded-lg border p-4"
                        style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)' }}
                      >
                        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                          <span className="flex items-center gap-2.5">
                            <span
                              aria-hidden
                              className="block h-2.5 w-2.5 shrink-0 rounded-full"
                              style={{ background: c.base }}
                            />
                            <span className="text-[13.5px] font-semibold text-[var(--text-primary)]">
                              {r.component ?? `${r.project} — all components`}
                            </span>
                            <span className="text-[12px] text-[var(--text-secondary)]">
                              {r.project}
                            </span>
                          </span>

                          <span className="flex items-center gap-2.5">
                            <span
                              className="rounded-full px-2 py-0.5 text-[10.5px]"
                              style={{
                                background: 'rgb(255 255 255 / 0.06)',
                                color: 'var(--text-secondary)',
                              }}
                            >
                              {wasteLabel[r.type] ?? r.type}
                            </span>
                            <span
                              className="tnum text-[15px] font-semibold"
                              style={{ color: TONE_CSS[WASTE_TONE[r.type]] }}
                            >
                              {formatMoney(r.amountRupees)}
                            </span>
                          </span>
                        </div>

                        <p className="mt-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">
                          {r.detail}
                        </p>
                      </motion.li>
                    );
                  })}
                </ul>
              </GlassCard>

              <GlassCard className="p-7">
                <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
                  Where it is concentrated
                </h2>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                  Waste already spent, and value still at risk, by project.
                </p>

                <div className="mt-6 flex flex-col gap-5">
                  {projectWaste.map((p, i) => {
                    const c = colorFor(palette, p.project);
                    const max = Math.max(
                      ...projectWaste.map((x) => x.recoverable + x.exposure),
                      1,
                    );
                    return (
                      <div key={p.project}>
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="flex items-center gap-2.5">
                            <span
                              aria-hidden
                              className="block h-2.5 w-2.5 rounded-full"
                              style={{ background: c.base }}
                            />
                            <span className="text-[13px] text-[var(--text-primary)]">
                              {p.project}
                            </span>
                          </span>
                          <span className="tnum text-[12.5px] text-[var(--text-secondary)]">
                            {formatMoney(p.recoverable + p.exposure)}
                          </span>
                        </div>

                        <span className="mt-2 flex h-2.5 w-full overflow-hidden rounded-full" style={{ background: 'rgb(255 255 255 / 0.05)' }}>
                          <motion.span
                            style={{ background: 'var(--amber)' }}
                            initial={{ width: 0 }}
                            whileInView={{ width: `${(p.recoverable / max) * 100}%` }}
                            viewport={{ once: true, amount: 0.2 }}
                            transition={{ duration: 0.8, ease: EASE_GLASS, delay: 0.08 * i }}
                          />
                          <motion.span
                            style={{ background: 'rgb(240 101 79 / 0.55)' }}
                            initial={{ width: 0 }}
                            whileInView={{ width: `${(p.exposure / max) * 100}%` }}
                            viewport={{ once: true, amount: 0.2 }}
                            transition={{ duration: 0.8, ease: EASE_GLASS, delay: 0.08 * i + 0.1 }}
                          />
                        </span>

                        <p className="mt-1.5 text-[11.5px] text-[var(--text-secondary)]">
                          {formatMoney(p.recoverable)} spent · {formatMoney(p.exposure)} at risk
                        </p>
                      </div>
                    );
                  })}
                </div>

                <div
                  className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 border-t pt-4 text-[11.5px] text-[var(--text-secondary)]"
                  style={{ borderColor: 'var(--border)' }}
                >
                  <span className="flex items-center gap-2">
                    <span
                      aria-hidden
                      className="block h-2.5 w-4 rounded-sm"
                      style={{ background: 'var(--amber)' }}
                    />
                    Spent, produced nothing
                  </span>
                  <span className="flex items-center gap-2">
                    <span
                      aria-hidden
                      className="block h-2.5 w-4 rounded-sm"
                      style={{ background: 'rgb(240 101 79 / 0.55)' }}
                    />
                    At risk, not spent
                  </span>
                </div>
              </GlassCard>
            </div>

            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">
              All figures computed from the event log. Ownership concentration is measured on
              pseudonymised author identifiers — this product renders no per-person figure and names
              no individual on any screen.
            </p>
          </motion.div>
        )}
      </div>
    </>
  );
}

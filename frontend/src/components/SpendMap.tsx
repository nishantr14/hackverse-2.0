import { AnimatePresence, motion } from 'framer-motion';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { WasteRow } from '../data/types';
import { formatMoney, formatPercent, formatRupeesExact, wasteLabel } from '../lib/format';
import { EASE_GLASS, snap } from '../lib/motion';
import { colorFor, type ProjectPalette } from '../lib/projectColors';
import type { ComponentSpend, ProjectSpend } from '../lib/spend';
import { wasteKey } from '../lib/spend';
import { squarify, type TreemapRect } from '../lib/treemap';

/**
 * The spend map.
 *
 * Area encodes rupees; hue encodes which project owns the territory. Colour is
 * doing identity work here, not magnitude work — every tile in a project shares
 * one hue, so the map reads as territories rather than as a bar chart in
 * disguise. Palette and its validation live in lib/projectColors.
 *
 * Tiles keep a stable key across both groupings, so switching grouping moves
 * the same tiles to new positions rather than tearing the map down and
 * rebuilding it. The rearrangement is the point: it is the same money, cut a
 * different way.
 */

const GAP = 5;
const HEADER_H = 34;

type Mode = 'hierarchy' | 'component';

interface Tile {
  key: string;
  rect: TreemapRect<ComponentSpend>;
  component: ComponentSpend;
  flagged: WasteRow[];
}

function useMeasure<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;

    // Measure once, synchronously. ResizeObserver only reports on a *change*,
    // and if its first delivery is missed the map renders empty.
    const rect = node.getBoundingClientRect();
    setSize({ width: rect.width, height: rect.height });

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, size] as const;
}

function inset(r: { x: number; y: number; w: number; h: number }, by: number) {
  return {
    x: r.x + by,
    y: r.y + by,
    w: Math.max(0, r.w - by * 2),
    h: Math.max(0, r.h - by * 2),
  };
}

interface SpendMapProps {
  projects: ProjectSpend[];
  components: ComponentSpend[];
  total: number;
  flagged: Map<string, WasteRow[]>;
  mode: Mode;
  palette: ProjectPalette;
  focusProject: string | null;
  onFocusProject: (project: string | null) => void;
  /** Reports the component under the cursor, so the headline can describe it. */
  onFocusComponent: (component: ComponentSpend | null) => void;
}

export function SpendMap({
  projects,
  components,
  total,
  flagged,
  mode,
  palette,
  focusProject,
  onFocusProject,
  onFocusComponent,
}: SpendMapProps) {
  const [ref, { width, height }] = useMeasure<HTMLDivElement>();
  const [active, setActive] = useState<string | null>(null);

  const { groups, tiles } = useMemo(() => {
    if (width < 2 || height < 2)
      return { groups: [] as TreemapRect<ProjectSpend>[], tiles: [] as Tile[] };

    if (mode === 'component') {
      const rects = squarify(
        components.map((c) => ({ value: c.cost, datum: c })),
        0,
        0,
        width,
        height,
      );
      return {
        groups: [],
        tiles: rects.map((rect) => {
          const key = wasteKey(rect.datum.project, rect.datum.component);
          return { key, rect, component: rect.datum, flagged: flagged.get(key) ?? [] };
        }),
      };
    }

    const groupRects = squarify(
      projects.map((p) => ({ value: p.cost, datum: p })),
      0,
      0,
      width,
      height,
    );

    const childTiles: Tile[] = [];
    for (const group of groupRects) {
      const box = inset(group, GAP);
      const showHeader = box.h > HEADER_H + 30;
      const inner = showHeader
        ? { x: box.x + 6, y: box.y + HEADER_H, w: box.w - 12, h: box.h - HEADER_H - 6 }
        : box;

      const rects = squarify(
        group.datum.components.map((c) => ({ value: c.cost, datum: c })),
        inner.x,
        inner.y,
        inner.w,
        inner.h,
      );

      for (const rect of rects) {
        const key = wasteKey(rect.datum.project, rect.datum.component);
        childTiles.push({ key, rect, component: rect.datum, flagged: flagged.get(key) ?? [] });
      }
    }

    return { groups: groupRects, tiles: childTiles };
  }, [projects, components, flagged, mode, width, height]);

  const activeTile = tiles.find((t) => t.key === active) ?? null;

  function focus(tile: Tile | null) {
    setActive(tile?.key ?? null);
    onFocusProject(tile?.component.project ?? null);
    onFocusComponent(tile?.component ?? null);
  }

  return (
    <div className="relative">
      <div
        ref={ref}
        className="relative h-[26rem] w-full"
        onMouseLeave={() => focus(null)}
      >
        {/* project territories */}
        <AnimatePresence>
          {groups.map((g) => {
            const box = inset(g, GAP);
            const showHeader = box.h > HEADER_H + 30;
            const c = colorFor(palette, g.datum.project);
            const dim = focusProject !== null && focusProject !== g.datum.project;
            return (
              <motion.div
                key={`group-${g.datum.project}`}
                className="pointer-events-none absolute rounded-xl border"
                initial={{ opacity: 0 }}
                animate={{ opacity: dim ? 0.34 : 1 }}
                exit={{ opacity: 0 }}
                transition={snap}
                style={{
                  left: box.x,
                  top: box.y,
                  width: box.w,
                  height: box.h,
                  borderColor: `rgb(${c.rgb} / 0.4)`,
                  background: `rgb(${c.rgb} / 0.07)`,
                }}
              >
                {showHeader && (
                  <div className="flex items-center justify-between gap-2 px-3 pt-2.5">
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        aria-hidden
                        className="block h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ background: c.base }}
                      />
                      <span className="truncate text-[13px] font-semibold text-[var(--text-primary)]">
                        {g.datum.project}
                      </span>
                    </span>
                    <span className="tnum shrink-0 text-[12px] text-[var(--text-secondary)]">
                      {formatMoney(g.datum.cost)}
                    </span>
                  </div>
                )}
              </motion.div>
            );
          })}
        </AnimatePresence>

        {/* component tiles */}
        {tiles.map((tile, i) => {
          const box = inset(tile.rect, 2);
          const c = colorFor(palette, tile.component.project);
          const isActive = tile.key === active;
          const hasWaste = tile.flagged.length > 0;
          const roomForText = box.w >= 92 && box.h >= 52;
          const big = box.w >= 190 && box.h >= 120;
          const dim = focusProject !== null && focusProject !== tile.component.project;
          // Resting fills are quiet; full strength is reserved for the legend
          // chips and whatever you are pointing at. Saturated blocks across the
          // whole map read as candy rather than as a budget.
          const fill = isActive ? 0.34 : 0.15;

          return (
            <motion.button
              key={tile.key}
              type="button"
              layout
              initial={{ opacity: 0, scale: 0.94 }}
              animate={{ opacity: dim ? 0.32 : 1, scale: 1 }}
              transition={{
                layout: { duration: 0.6, ease: EASE_GLASS },
                default: { duration: 0.45, ease: EASE_GLASS, delay: 0.035 * i },
                opacity: { duration: 0.25, ease: EASE_GLASS },
              }}
              whileHover={{ y: -4 }}
              onMouseEnter={() => focus(tile)}
              onFocus={() => focus(tile)}
              onBlur={() => focus(null)}
              // `flex items-start` is load-bearing: a bare <button> centres its
              // content vertically, parking every label mid-tile.
              className="absolute flex items-start overflow-hidden rounded-lg border text-left"
              style={{
                left: box.x,
                top: box.y,
                width: box.w,
                height: box.h,
                zIndex: isActive ? 20 : 1,
                background: `linear-gradient(155deg, rgb(${c.rgb} / ${fill + 0.06}), rgb(${c.rgb} / ${fill}) 60%), var(--bg-raised)`,
                borderColor: `rgb(${c.rgb} / ${isActive ? 0.85 : 0.34})`,
                boxShadow: isActive
                  ? `0 18px 44px -18px rgb(${c.rgb} / 0.6), inset 0 1px 0 rgb(255 255 255 / 0.1)`
                  : 'inset 0 1px 0 rgb(255 255 255 / 0.05)',
              }}
              aria-label={`${tile.component.component}, ${tile.component.project}, ${formatRupeesExact(
                tile.component.cost,
              )}, ${formatPercent(tile.component.cost / total)} of total spend${
                hasWaste ? ', flagged on the waste screen' : ''
              }`}
            >
              {hasWaste && (
                <span
                  aria-hidden
                  className="absolute top-0 right-0 h-0 w-0"
                  style={{
                    borderTop: '18px solid var(--amber)',
                    borderLeft: '18px solid transparent',
                  }}
                />
              )}

              {roomForText && (
                <span className={`block w-full min-w-0 ${big ? 'px-4 py-3.5' : 'px-3 py-2.5'}`}>
                  <span
                    className={`tnum block leading-none font-semibold text-[var(--text-primary)] ${
                      big ? 'text-[28px] tracking-[-0.02em]' : 'text-[16px] tracking-[-0.01em]'
                    }`}
                  >
                    {formatMoney(tile.component.cost)}
                  </span>
                  <span
                    className={`mt-2 block truncate font-medium text-[var(--text-primary)] ${
                      big ? 'text-[14px]' : 'text-[12.5px]'
                    }`}
                  >
                    {tile.component.component}
                  </span>
                  {(big || mode === 'component') && box.h >= 78 && (
                    <span className="mt-1 block truncate text-[11.5px] text-[var(--text-secondary)]">
                      {mode === 'component' ? `${tile.component.project} · ` : ''}
                      {formatPercent(tile.component.cost / total, 0)} of spend
                    </span>
                  )}
                </span>
              )}
            </motion.button>
          );
        })}
      </div>

      {/* Inspector.
          In flow under the map, not floating over it: as an absolutely
          positioned card it covered the tiles on the right and the legend
          underneath. The height is reserved so pointing at a tile never
          reflows the page. */}
      <div
        className="mt-4 min-h-[88px] border-t pt-3.5"
        style={{ borderColor: 'var(--border)' }}
      >
        {/* A keyed element rather than AnimatePresence: `mode="wait"` holds the
            incoming child until the outgoing one finishes exiting, and an exit
            that never settles leaves the slot permanently empty. Keying on the
            tile is enough — the content swaps and re-runs its entrance. */}
        {activeTile ? (
          <motion.div
            key={activeTile.key}
            role="status"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={snap}
          >
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1.5">
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: colorFor(palette, activeTile.component.project).base }}
                />
                <span className="text-[14px] font-semibold text-[var(--text-primary)]">
                  {activeTile.component.component}
                </span>
              </span>
              <span className="text-[12.5px] text-[var(--text-secondary)]">
                {activeTile.component.project}
              </span>

              <span className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[12.5px]">
                <Fact term="Cost" value={formatRupeesExact(activeTile.component.cost)} />
                <Fact term="Authoring" value={`${activeTile.component.authorHours} h`} />
                <Fact term="Review" value={`${activeTile.component.reviewHours} h`} />
                <Fact term="Work items" value={activeTile.component.workItems.join(', ')} />
              </span>
            </div>

            <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
              Cost = observed engineer-hours × role-band rate, summed over those work items.
              {activeTile.flagged.length > 0 && (
                <span style={{ color: 'var(--amber)' }}>
                  {' '}
                  Flagged: {activeTile.flagged.map((w) => wasteLabel[w.type] ?? w.type).join(', ')} —{' '}
                  {formatMoney(activeTile.flagged.reduce((s, w) => s + w.amountRupees, 0))}.
                </span>
              )}
            </p>
          </motion.div>
        ) : (
          <motion.p
            key="idle"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={snap}
            className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]"
          >
            Point at any territory to price it — cost, the hours behind it, and the work items it
            came from.
          </motion.p>
        )}
      </div>

      {/* map key — the projects, in the order the palette assigns them */}
      <div
        className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 border-t pt-4"
        style={{ borderColor: 'var(--border)' }}
      >
        {projects.map((p) => {
          const c = colorFor(palette, p.project);
          const dim = focusProject !== null && focusProject !== p.project;
          return (
            <motion.button
              key={p.project}
              type="button"
              className="flex items-center gap-2.5 rounded-md px-1 py-0.5"
              animate={{ opacity: dim ? 0.4 : 1 }}
              transition={snap}
              onMouseEnter={() => onFocusProject(p.project)}
              onMouseLeave={() => onFocusProject(null)}
              onFocus={() => onFocusProject(p.project)}
              onBlur={() => onFocusProject(null)}
            >
              <span
                aria-hidden
                className="block h-3 w-3 rounded-[4px]"
                style={{ background: c.base }}
              />
              <span className="text-[12.5px] text-[var(--text-primary)]">{p.project}</span>
              <span className="tnum text-[12.5px] text-[var(--text-secondary)]">
                {formatMoney(p.cost)}
              </span>
            </motion.button>
          );
        })}

        <span className="flex items-center gap-2.5 text-[12.5px] text-[var(--text-secondary)]">
          <span
            aria-hidden
            className="block h-0 w-0"
            style={{ borderTop: '12px solid var(--amber)', borderLeft: '12px solid transparent' }}
          />
          Waste flagged on screen 03
        </span>
      </div>

    </div>
  );
}

/** One labelled figure in the inspector strip. */
function Fact({ term, value }: { term: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-[var(--text-secondary)]">{term}</span>
      <span className="tnum text-[var(--text-primary)]">{value}</span>
    </span>
  );
}

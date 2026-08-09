import { motion } from 'framer-motion';
import type { ProcessEdge, ProcessGraph } from '../data/types';
import { formatMoney } from '../lib/format';
import { EASE_GLASS, snap } from '../lib/motion';
import { CANVAS, NODE, NODE_POS, drawEdges, variantTone, VARIANT_LABEL } from '../lib/process';

/**
 * The process map.
 *
 * Thickness is rupees. On a conventional process map thickness is frequency,
 * and on this data the two tell opposite stories — the triple-review detour is
 * a hairline by count and a rope by cost. Drawing money is the entire reason
 * this screen exists, so it is the first thing on the screen, drawn large.
 *
 * SVG rather than a chart library: nine edges with a hand-placed layout is less
 * code than configuring a graph renderer, and it means the stroke width is
 * literally the cost scale rather than something a library decided.
 */

interface ProcessMapProps {
  graph: ProcessGraph;
  /** Null shows every variant at once. */
  variant: string | null;
  activeEdge: ProcessEdge | null;
  onActiveEdge: (edge: ProcessEdge | null) => void;
}

export function ProcessMap({ graph, variant, activeEdge, onActiveEdge }: ProcessMapProps) {
  const edges = drawEdges(graph, variant);
  const detour = NODE_POS.changes_requested;

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${CANVAS.w} ${CANVAS.h}`}
        className="w-full"
        style={{ overflow: 'visible' }}
        role="img"
        aria-label="Delivery process map. Line thickness is cost, not frequency."
      >
        <defs>
          {/* One gradient per edge that carries more than one variant — a hard
              stop between pure colours, never a blend. Blending coral into
              amber would invent a muddy hue that means nothing in the
              palette; a hard edge keeps both colours legible and shows the
              split honestly. */}
          {edges
            .filter((d) => d.shares.length > 1)
            .map((d) => {
              let acc = 0;
              return (
                <linearGradient
                  key={`grad-${d.key}`}
                  id={`grad-${d.key}`}
                  gradientUnits="userSpaceOnUse"
                  x1={d.p1.x}
                  y1={d.p1.y}
                  x2={d.p2.x}
                  y2={d.p2.y}
                >
                  {d.shares.flatMap((s, i) => {
                    const from = acc;
                    acc += s.share * 100;
                    const css = variantTone(s.variant).css;
                    return [
                      <stop key={`${i}-a`} offset={`${from}%`} stopColor={css} />,
                      <stop key={`${i}-b`} offset={`${acc}%`} stopColor={css} />,
                    ];
                  })}
                </linearGradient>
              );
            })}

          {['happy_path', 'rework_loop', 'triple_review'].map((v) => (
            <marker
              key={v}
              id={`arrow-${v}`}
              viewBox="0 0 10 10"
              refX="8.5"
              refY="5"
              // Fixed size in SVG units, not "strokeWidth" units (the default).
              // The default scales the arrowhead WITH the line's own stroke
              // width, so the widest edges — exactly the ones the screen exists
              // to point at — grew a marker wide enough to read as its own
              // shape and swallow the node it was pointing at.
              markerUnits="userSpaceOnUse"
              markerWidth="16"
              markerHeight="16"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill={variantTone(v).css} />
            </marker>
          ))}
          <radialGradient id="detour-zone" cx="50%" cy="35%" r="65%">
            <stop offset="0%" stopColor="rgb(245 166 35 / 0.10)" />
            <stop offset="100%" stopColor="rgb(245 166 35 / 0)" />
          </radialGradient>
        </defs>

        {/* a soft wash behind the detour, so "this is a different zone of the
            process" reads before you trace a single line into it */}
        <ellipse cx={detour.cx} cy={detour.cy + 30} rx={340} ry={220} fill="url(#detour-zone)" />

        {edges.map((d, i) => {
          const tone = variantTone(d.edge.variant);
          // One line per transition now, so the node pair identifies it.
          const isActive =
            activeEdge !== null &&
            activeEdge.from === d.edge.from &&
            activeEdge.to === d.edge.to;
          const dim = activeEdge !== null && !isActive;

          return (
            <g key={d.key}>
              {/* A fat invisible copy of the path so thin edges are still
                  reasonably easy to point at. */}
              <path
                d={d.path}
                fill="none"
                stroke="transparent"
                strokeWidth={Math.max(d.width, 24)}
                style={{ cursor: 'pointer' }}
                onMouseEnter={() => onActiveEdge(d.edge)}
                onMouseLeave={() => onActiveEdge(null)}
              />
              <motion.path
                d={d.path}
                fill="none"
                stroke={d.shares.length > 1 ? `url(#grad-${d.key})` : tone.css}
                strokeWidth={d.width}
                strokeLinecap="round"
                markerEnd={`url(#arrow-${d.edge.variant})`}
                pointerEvents="none"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{
                  pathLength: 1,
                  opacity: dim ? 0.22 : isActive ? 1 : 0.8,
                }}
                transition={{
                  pathLength: { duration: 0.9, ease: EASE_GLASS, delay: 0.06 * i },
                  opacity: snap,
                }}
              />
              {(isActive || d.width > 9) && (
                <motion.g
                  initial={{ opacity: 0 }}
                  animate={{ opacity: dim ? 0.3 : 1 }}
                  transition={snap}
                >
                  <rect
                    x={d.label.x - 42}
                    y={d.label.y - 30}
                    width={84}
                    height={22}
                    rx={11}
                    fill="rgb(11 14 20 / 0.82)"
                  />
                  <text
                    x={d.label.x}
                    y={d.label.y - 14}
                    textAnchor="middle"
                    pointerEvents="none"
                    className="tnum"
                    style={{ fontSize: 15, fontWeight: 700, fill: tone.css }}
                  >
                    {formatMoney(d.edge.costRupees)}
                  </text>
                </motion.g>
              )}
            </g>
          );
        })}

        {graph.nodes.map((node, i) => {
          const p = NODE_POS[node.id];
          if (!p) return null;
          const touched =
            activeEdge !== null && (activeEdge.from === node.id || activeEdge.to === node.id);
          const isDetour = node.id === 'changes_requested';
          const tone = isDetour ? variantTone('rework_loop') : null;

          return (
            <motion.g
              key={node.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, ease: EASE_GLASS, delay: 0.05 * i }}
            >
              <motion.rect
                x={p.cx - NODE.w / 2}
                y={p.cy - NODE.h / 2}
                width={NODE.w}
                height={NODE.h}
                rx={14}
                animate={{
                  fill: touched
                    ? isDetour
                      ? 'rgb(245 166 35 / 0.16)'
                      : 'rgb(40 48 66)'
                    : isDetour
                      ? 'rgb(245 166 35 / 0.08)'
                      : 'rgb(30 36 50)',
                  stroke: touched
                    ? isDetour
                      ? 'rgb(245 166 35 / 0.75)'
                      : 'rgb(232 236 244 / 0.55)'
                    : isDetour
                      ? 'rgb(245 166 35 / 0.4)'
                      : 'rgb(56 66 90)',
                }}
                transition={snap}
                strokeWidth={1.75}
              />
              <text
                x={p.cx}
                y={p.cy + 6}
                textAnchor="middle"
                style={{
                  fontSize: 16,
                  fontWeight: 700,
                  fill: tone && !touched ? tone.css : 'var(--text-primary)',
                  pointerEvents: 'none',
                }}
              >
                {node.label}
              </text>
            </motion.g>
          );
        })}
      </svg>

      <div
        className="mt-5 flex flex-wrap items-center gap-x-7 gap-y-2 border-t pt-4"
        style={{ borderColor: 'var(--border)' }}
      >
        {['happy_path', 'rework_loop', 'triple_review'].map((v) => (
          <span key={v} className="flex items-center gap-2.5 text-[13px]">
            <span
              aria-hidden
              className="block h-[4px] w-8 rounded-full"
              style={{ background: variantTone(v).css }}
            />
            <span className="text-[var(--text-primary)]">{VARIANT_LABEL[v]}</span>
          </span>
        ))}
        <span className="text-[12.5px] text-[var(--text-secondary)]">
          Line thickness is rupees, not how often the step runs.
        </span>
      </div>
    </div>
  );
}

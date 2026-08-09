/**
 * Derived process aggregates and the map's geometry.
 *
 * The graph is weighted by COST, not frequency. That inversion is the whole
 * point of the screen: the transition a team runs most often is not the one
 * that takes the most money, and you cannot see that on a conventional process
 * map where thickness means "how often".
 */

import type { ProcessEdge, ProcessGraph, VariantSummary } from '../data/types';

export const VARIANT_LABEL: Record<string, string> = {
  happy_path: 'Happy path',
  rework_loop: 'Rework loop',
  triple_review: 'Triple review',
};

/**
 * Variant colour.
 *
 * All three of the app's semantic hues, and no others — the spec's rule is to
 * reuse an existing meaning before inventing a new one, and the happy path
 * already has one waiting: teal is "on time", and the happy path is exactly
 * the work that shipped without a rework loop or an extra review round. Amber
 * for waste, coral for the delay/risk path.
 */
export const VARIANT_TONE: Record<string, { rgb: string; css: string }> = {
  happy_path: { rgb: '45 212 191', css: 'var(--teal)' },
  rework_loop: { rgb: '245 166 35', css: 'var(--amber)' },
  triple_review: { rgb: '240 101 79', css: 'var(--coral)' },
};

export function variantTone(variant: string) {
  return VARIANT_TONE[variant] ?? VARIANT_TONE.happy_path;
}

export interface VariantStat extends VariantSummary {
  label: string;
  /** Cost share ÷ work-item share. Above 1 means it costs more than its weight. */
  costMultiple: number;
  edges: ProcessEdge[];
  cost: number;
  passes: number;
}

export function variantStats(graph: ProcessGraph): VariantStat[] {
  return graph.variantSummary
    .map((v) => {
      const edges = graph.edges.filter((e) => e.variant === v.variant);
      return {
        ...v,
        label: VARIANT_LABEL[v.variant] ?? v.variant,
        costMultiple: v.shareOfWorkItems > 0 ? v.shareOfCost / v.shareOfWorkItems : 0,
        edges,
        cost: edges.reduce((s, e) => s + e.costRupees, 0),
        passes: edges.reduce((s, e) => s + e.frequency, 0),
      };
    })
    .sort((a, b) => b.costMultiple - a.costMultiple);
}

/** Share of cost that does not sit on the happy path. */
export function offHappyPathCostShare(graph: ProcessGraph): number {
  return graph.variantSummary
    .filter((v) => v.variant !== 'happy_path')
    .reduce((s, v) => s + v.shareOfCost, 0);
}

/** Every return trip to review, across all rework variants. */
export function reworkPasses(graph: ProcessGraph): number {
  return graph.edges
    .filter((e) => e.to === 'changes_requested')
    .reduce((s, e) => s + e.frequency, 0);
}

/**
 * How many work items the log covers.
 *
 * Everything commits exactly once, so the transitions leaving `commit` are a
 * headcount of the work items. That is what makes the variant shares
 * expressible as item counts rather than as bare percentages — 412 × 0.60
 * lands on the 248 that the review → merge edge independently records, which
 * is the check that this reading is right.
 */
export function totalWorkItems(graph: ProcessGraph): number {
  return graph.edges.filter((e) => e.from === 'commit').reduce((s, e) => s + e.frequency, 0);
}

/**
 * The ordered steps a variant actually takes, walked from its own edges.
 *
 * Nothing is assumed about what a path "should" look like: the walk follows
 * recorded transitions and stops following when the next edge does not start
 * where the last one ended. For the rework variants this yields the detour
 * itself — review → changes requested → review — rather than a whole journey
 * the fixture never claims, and the repeated steps are exactly what makes the
 * expensive path visibly longer than the cheap one.
 */
export function chainFor(graph: ProcessGraph, variant: string): string[] {
  const edges = graph.edges.filter((e) => e.variant === variant);
  if (edges.length === 0) return [];

  const steps = [edges[0].from];
  for (const e of edges) {
    if (e.from !== steps[steps.length - 1]) break;
    steps.push(e.to);
  }
  return steps;
}

/** Plain-English gloss of what each path means. Judges do not read node graphs. */
export const VARIANT_MEANING: Record<string, string> = {
  happy_path: 'Straight through. Written, reviewed once, merged, shipped.',
  rework_loop: 'Sent back once. The reviewer asked for changes, then approved.',
  triple_review: 'Sent back twice. Three rounds of review before it could merge.',
};

/* ---------------------------------------------------------------------------
   Geometry

   Hand-placed rather than force-directed. Five nodes with a known reading
   order — commit on the left, deploy on the right, the rework detour lifted
   above the spine — beats any layout algorithm here, and it is stable across
   renders, which a force layout is not.
   --------------------------------------------------------------------------- */

export const CANVAS = { w: 1680, h: 620 };
export const NODE = { w: 150, h: 66 };

/**
 * ONE SPINE, not two lanes.
 *
 * The ticket lifecycle and the git/PR flow were drawn as separate rows,
 * which was tidy and wrong: they are the same process. A ticket is created,
 * someone commits against it, it is reviewed, merged, and then the ticket is
 * resolved. Splitting that across two rows meant its busiest links —
 * ticket_created → review, merged → ticket_resolved, commit →
 * ticket_resolved — became long diagonals cutting across everything, and
 * one of them ran straight through the Changes Requested box.
 *
 * Laid out end to end instead, the real process reads left to right in one
 * line, and the only things off the spine are the two ways work goes
 * backwards (DETOUR_Y, above) and activities nobody performs (BELOW_Y).
 *
 * The fixture had five hand-placed nodes and used `merge`. The canonical
 * spelling is `merged` — locked decision #14. A node id that is not in this
 * map used to crash the whole screen on `NODE_POS[id].cx`; posFor() below
 * makes that impossible.
 */
const DETOUR_Y = 180;
const SPINE_Y = 380;
const BELOW_Y = 560;

export const NODE_POS: Record<string, { cx: number; cy: number }> = {
  // The process, end to end, in the order it happens.
  ticket_created: { cx: 90, cy: SPINE_Y },
  ticket_started: { cx: 300, cy: SPINE_Y },
  commit: { cx: 510, cy: SPINE_Y },
  review_requested: { cx: 720, cy: SPINE_Y },
  review: { cx: 930, cy: SPINE_Y },
  approved: { cx: 1140, cy: SPINE_Y },
  merged: { cx: 1350, cy: SPINE_Y },
  ticket_resolved: { cx: 1560, cy: SPINE_Y },

  // Going backwards, above the line, each sitting over the step it returns to.
  ticket_in_review: { cx: 405, cy: DETOUR_Y },
  force_push: { cx: 825, cy: DETOUR_Y },
  changes_requested: { cx: 1035, cy: DETOUR_Y },
  reopened: { cx: 1245, cy: DETOUR_Y },
  ticket_closed: { cx: 1455, cy: DETOUR_Y },

  // Not a step anyone performs. Absent from the default view, so the space
  // under the spine stays free for the skip-arcs drawn there.
  ci_run: { cx: 405, cy: BELOW_Y },
  deploy: { cx: 1245, cy: BELOW_Y },
  meeting: { cx: 825, cy: BELOW_Y },
  ticket_reopened: { cx: 1455, cy: BELOW_Y },
};

/**
 * Position of a node, with a deterministic parking spot for ids this map has
 * never heard of.
 *
 * A new activity should make the map slightly untidy, never blank. The
 * fallback is derived from the id itself so it does not move between
 * renders — an unplaced node that jitters is worse than one sitting in an
 * odd but stable place.
 */
export function posFor(id: string): { cx: number; cy: number } {
  const known = NODE_POS[id];
  if (known) return known;
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  const slot = Math.abs(hash) % 6;
  return { cx: 240 + slot * 240, cy: BELOW_Y };
}

export interface DrawnEdge {
  key: string;
  edge: ProcessEdge;
  path: string;
  width: number;
  /** Point on the curve for the cost label. */
  label: { x: number; y: number };
  /** The line's own start/end points, for building a gradient along it. */
  p1: { x: number; y: number };
  p2: { x: number; y: number };
  /**
   * Each contributing variant's share of this line's cost, desc, summing to 1.
   * A merged transition is drawn as a hard-stop gradient across these shares
   * rather than a single flat colour — the alternative was picking one
   * "dominant" variant and quietly erasing the others from the picture, which
   * is exactly the kind of thing this screen exists to stop happening to money.
   */
  shares: { variant: string; share: number }[];
}

/**
 * A curve between two node boundaries, bowed sideways by `k`.
 *
 * Reversing the direction flips the normal, so drawing A→B and B→A with the
 * same `k` produces two arcs bowing to opposite sides. That is what makes the
 * rework loop read as a round trip rather than as one line drawn twice.
 */
function bow(x1: number, y1: number, x2: number, y2: number, k: number) {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const cx = mx + (-dy / len) * k;
  const cy = my + (dx / len) * k;
  return { path: `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`, label: { x: cx, y: cy } };
}

/** Where a line from `from` toward `to` leaves the `from` box. */
function anchor(from: string, to: string) {
  const a = posFor(from);
  const b = posFor(to);
  const dx = b.cx - a.cx;
  const dy = b.cy - a.cy;
  const hw = NODE.w / 2 + 4;
  const hh = NODE.h / 2 + 4;
  // Leave through whichever face the direction points at most strongly.
  if (Math.abs(dx) * hh > Math.abs(dy) * hw) {
    return { x: a.cx + Math.sign(dx) * hw, y: a.cy + (dy / Math.abs(dx || 1)) * hw * 0.35 };
  }
  return { x: a.cx + (dx / Math.abs(dy || 1)) * hh * 0.35, y: a.cy + Math.sign(dy) * hh };
}

/**
 * How many node boxes a straight line between these two would plough through.
 *
 * `commit -> merged` is drawn along the spine, and the spine also holds
 * review_requested, review and approved between them — so the straight line
 * ran through all three boxes, which read as an edge touching five nodes
 * rather than one edge skipping three.
 */
function nodesBetween(from: string, to: string): number {
  const a = posFor(from);
  const b = posFor(to);
  const others = Object.entries(NODE_POS).filter(([id]) => id !== from && id !== to);

  // Walk the segment and count boxes it lands inside. Sampling rather than
  // analytic intersection because it is obviously correct at a glance and
  // the geometry here is tens of segments, not thousands — an earlier
  // same-row-only test missed commit -> ticket_resolved, a long diagonal
  // that ran straight through the Changes Requested box.
  const hw = NODE.w / 2;
  const hh = NODE.h / 2;
  const hit = new Set<string>();
  const STEPS = 48;
  for (let i = 1; i < STEPS; i += 1) {
    const t = i / STEPS;
    const x = a.cx + (b.cx - a.cx) * t;
    const y = a.cy + (b.cy - a.cy) * t;
    for (const [id, p] of others) {
      if (Math.abs(x - p.cx) < hw && Math.abs(y - p.cy) < hh) hit.add(id);
    }
  }
  return hit.size;
}

/**
 * Lays out one line per transition.
 *
 * Parallel edges are merged. Three variants record a review → changes
 * requested transition, and drawing them separately put six arcs between the
 * same two boxes with their cost labels stacked on top of each other — the map
 * became less legible the more the data had to say. One line per transition,
 * carrying the summed cost and tinted by whichever variant contributes most of
 * it, says the same thing and can actually be read.
 *
 * Stroke width is scaled against the widest transition in the WHOLE graph, not
 * within the current filter, so isolating the cheapest route does not redraw it
 * at full thickness and quietly lie about its size.
 */
export function drawEdges(graph: ProcessGraph, variant: string | null): DrawnEdge[] {
  const aggregate = (edges: ProcessEdge[]) => {
    const groups = new Map<string, ProcessEdge[]>();
    for (const e of edges) {
      const pair = `${e.from}->${e.to}`;
      const list = groups.get(pair);
      if (list) list.push(e);
      else groups.set(pair, [e]);
    }
    return [...groups.values()].map((list) => {
      // Summed per variant, not per single edge. Review → changes requested
      // carries one rework_loop edge at ₹11.3L and two triple_review edges at
      // ₹6.9L + ₹6.4L — the single richest edge is rework_loop, but
      // triple_review's ₹13.3L combined is the bigger share of that line.
      // Picking a "dominant" variant by single-edge size would have coloured
      // it rework_loop and quietly erased triple_review from the diagram on
      // both of its transitions. Both stay, drawn as shares of the line.
      const byVariant = new Map<string, number>();
      for (const e of list) byVariant.set(e.variant, (byVariant.get(e.variant) ?? 0) + e.costRupees);
      const totalCost = [...byVariant.values()].reduce((s, c) => s + c, 0) || 1;
      const shares = [...byVariant.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([v, cost]) => ({ variant: v, share: cost / totalCost }));

      return {
        from: list[0].from,
        to: list[0].to,
        frequency: list.reduce((s, e) => s + e.frequency, 0),
        costRupees: totalCost,
        variant: shares[0].variant,
        shares,
      };
    });
  };

  const allMerged = aggregate(graph.edges);
  const maxCost = Math.max(...allMerged.map((e) => e.costRupees), 1);
  const shown = aggregate(variant ? graph.edges.filter((e) => e.variant === variant) : graph.edges);

  return shown.map(({ shares, ...edge }) => {
    const skipped = nodesBetween(edge.from, edge.to);
    const isDetour = edge.from === 'changes_requested' || edge.to === 'changes_requested';

    let a = anchor(edge.from, edge.to);
    let b = anchor(edge.to, edge.from);
    let path: string;
    let label: { x: number; y: number };

    if (skipped > 0) {
      /**
       * A skip leaves through the BOTTOM face and arcs under the spine.
       *
       * Bowing a spine-to-spine line was not enough on its own: a quadratic
       * hugs its endpoints, so `commit → merged` still clipped the first box
       * it passed even with a deep bow. Leaving downwards means it is clear
       * of the row from the first pixel.
       *
       * Depth grows with how many boxes are skipped, and returns swing
       * deeper than advances, so a pair like commit → merged and
       * merged → commit never sit on top of each other.
       */
      const from = posFor(edge.from);
      const to = posFor(edge.to);
      const forward = to.cx > from.cx;
      const depth = forward ? 50 + skipped * 16 : 100 + skipped * 28;

      a = { x: from.cx, y: from.cy + NODE.h / 2 + 4 };
      b = { x: to.cx, y: to.cy + NODE.h / 2 + 4 };
      const mx = (a.x + b.x) / 2;
      const control = Math.max(a.y, b.y) + 2 * depth;
      path = `M ${a.x} ${a.y} Q ${mx} ${control} ${b.x} ${b.y}`;
      label = { x: mx, y: (a.y + b.y) / 2 + depth };
    } else if (isDetour) {
      ({ path, label } = bow(a.x, a.y, b.x, b.y, 52));
    } else {
      path = `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
      label = { x: (a.x + b.x) / 2, y: a.y };
    }

    return {
      key: `${edge.from}->${edge.to}`,
      edge,
      path,
      width: 4 + (edge.costRupees / maxCost) * 28,
      label,
      p1: { x: a.x, y: a.y },
      p2: { x: b.x, y: b.y },
      shares,
    };
  });
}

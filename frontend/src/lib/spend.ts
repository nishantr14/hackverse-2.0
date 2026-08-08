/**
 * Derived spend aggregates.
 *
 * Everything on the spend screen is computed here from the raw event-log rows —
 * nothing is a literal in a component. That is the point of the screen: the
 * figures have to trace back to rows.
 */

import type { SpendRow, WasteRow } from '../data/types';

export interface ComponentSpend {
  component: string;
  project: string;
  cost: number;
  authorHours: number;
  reviewHours: number;
  workItems: string[];
}

export interface ProjectSpend {
  project: string;
  cost: number;
  authorHours: number;
  reviewHours: number;
  workItems: number;
  components: ComponentSpend[];
}

export interface SpendTotals {
  cost: number;
  authorHours: number;
  reviewHours: number;
  totalHours: number;
  workItems: number;
  projects: number;
  components: number;
  /** Blended rupees per engineer-hour across every priced hour in the log. */
  blendedRate: number;
  /** Share of all engineer-hours spent reviewing rather than authoring. */
  reviewShare: number;
}

export function totals(rows: SpendRow[]): SpendTotals {
  const cost = rows.reduce((s, r) => s + r.cost, 0);
  const authorHours = rows.reduce((s, r) => s + r.authorHours, 0);
  const reviewHours = rows.reduce((s, r) => s + r.reviewHours, 0);
  const totalHours = authorHours + reviewHours;

  return {
    cost,
    authorHours,
    reviewHours,
    totalHours,
    workItems: rows.length,
    projects: new Set(rows.map((r) => r.project)).size,
    components: new Set(rows.map((r) => `${r.project}/${r.component}`)).size,
    blendedRate: totalHours > 0 ? cost / totalHours : 0,
    reviewShare: totalHours > 0 ? reviewHours / totalHours : 0,
  };
}

export function byProject(rows: SpendRow[]): ProjectSpend[] {
  const projects = new Map<string, ProjectSpend>();

  for (const row of rows) {
    let project = projects.get(row.project);
    if (!project) {
      project = {
        project: row.project,
        cost: 0,
        authorHours: 0,
        reviewHours: 0,
        workItems: 0,
        components: [],
      };
      projects.set(row.project, project);
    }

    project.cost += row.cost;
    project.authorHours += row.authorHours;
    project.reviewHours += row.reviewHours;
    project.workItems += 1;

    let component = project.components.find((c) => c.component === row.component);
    if (!component) {
      component = {
        component: row.component,
        project: row.project,
        cost: 0,
        authorHours: 0,
        reviewHours: 0,
        workItems: [],
      };
      project.components.push(component);
    }

    component.cost += row.cost;
    component.authorHours += row.authorHours;
    component.reviewHours += row.reviewHours;
    component.workItems.push(row.workItem);
  }

  const out = [...projects.values()].sort((a, b) => b.cost - a.cost);
  for (const p of out) p.components.sort((a, b) => b.cost - a.cost);
  return out;
}

export function byComponent(rows: SpendRow[]): ComponentSpend[] {
  return byProject(rows)
    .flatMap((p) => p.components)
    .sort((a, b) => b.cost - a.cost);
}

/**
 * Which components the waste screen has flagged, so the spend map can mark
 * them. Same event log, joined on (project, component) — this is the only
 * cross-screen link in the app and it is a join, not a duplicated constant.
 */
export function flaggedComponents(waste: WasteRow[]): Map<string, WasteRow[]> {
  const map = new Map<string, WasteRow[]>();
  for (const row of waste) {
    if (!row.component) continue;
    const key = `${row.project}/${row.component}`;
    const list = map.get(key);
    if (list) list.push(row);
    else map.set(key, [row]);
  }
  return map;
}

export function wasteKey(project: string, component: string): string {
  return `${project}/${component}`;
}

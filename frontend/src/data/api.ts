/**
 * THE SWAP-IN POINT — now swapped in.
 *
 * Every screen reads its data through these functions and nothing else. No
 * component imports a JSON file, and no component knows a URL. That held, so
 * moving off fixtures onto the real backend changed only this file.
 *
 * The backend returns richer shapes than the UI needs (edge significance,
 * evidence SQL, per-detector unit notes). The mapping down to the frozen
 * contracts in ./types lives here on purpose: a screen should not have to
 * know which of three endpoints a number arrived on.
 *
 * ERRORS ARE NOT SWALLOWED. useAsync renders a failure panel; a screen quietly
 * showing zeros because a fetch failed is the one outcome worse than an error.
 */

import type {
  ProcessGraph,
  SimulatorInput,
  SimulatorOutput,
  SpendRow,
  WasteRow,
  WasteType,
} from './types';

const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
).replace(/\/$/, '');

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // The simulator answers "I cannot forecast that" with a 422 and a
    // sentence. Surface the sentence, not the status code.
    let detail = `${res.status} ${res.statusText}`;
    try {
      const payload = (await res.json()) as { detail?: unknown };
      if (typeof payload.detail === 'string') detail = payload.detail;
    } catch {
      /* non-JSON error body; keep the status line */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/* ------------------------------------------------------------------ spend */

export function getSpend(): Promise<SpendRow[]> {
  return get<SpendRow[]>('/spend?limit=10000');
}

export interface SpendSummary {
  totalCost: number;
  totalHours: number;
  byBasis: Record<string, { cost: number; kind: string }>;
  observedShare: number;
  blendedHourly: number;
  citation: { labour: string | null; ci: string | null; error: string | null };
  hoursNote: string;
}

export function getSpendSummary(): Promise<SpendSummary> {
  return get<SpendSummary>('/spend/summary');
}

/* ------------------------------------------------------------------ waste */

interface WasteByProjectRow {
  type: string;
  project: string;
  component: string | null;
  nItems: number;
  hours: number;
  amountRupees: number | null;
}

interface WasteByProject {
  rows: WasteByProjectRow[];
  unpriced: { latency: string | null; ci: string | null };
  keyPerson: string;
}

const KNOWN_WASTE: ReadonlySet<string> = new Set([
  'rework',
  'latency',
  'meeting',
  'ci',
]);

function describe(row: WasteByProjectRow): string {
  const where = row.component ? `${row.project}/${row.component}` : row.project;
  switch (row.type) {
    case 'rework':
      return `${row.nItems} change-request → redo pair${
        row.nItems === 1 ? '' : 's'
      } in ${where}, ${row.hours.toFixed(1)}h between request and redo.`;
    case 'latency':
      return `${row.nItems} PR${row.nItems === 1 ? '' : 's'} in ${where} waited ${(
        row.hours / 24
      ).toFixed(0)} days in total for a first review. Duration only — waiting is not billed.`;
    case 'meeting':
      return `${row.nItems} work item${
        row.nItems === 1 ? '' : 's'
      } in ${where} carried modelled meeting time.`;
    case 'ci':
      return `${row.nItems.toLocaleString()} rerun or failed CI runs in ${where}, ${row.hours.toFixed(
        0,
      )} runner-hours.`;
    default:
      return where;
  }
}

/**
 * `latency` arrives with a null amount and keeps it — waiting is wall clock,
 * not paid engineer time, and pricing it at a salary rate is how a waste
 * figure stops being defensible. `priced: false` is what the card reads to
 * show a duration instead of rupees.
 */
export async function getWaste(): Promise<WasteRow[]> {
  const data = await get<WasteByProject>('/waste/by-project');
  return data.rows
    .filter((r) => KNOWN_WASTE.has(r.type))
    .map((r) => ({
      type: r.type as WasteType,
      project: r.project,
      component: r.component,
      amountRupees: r.amountRupees ?? 0,
      priced: r.amountRupees !== null,
      hours: r.hours,
      nItems: r.nItems,
      detail: describe(r),
    }))
    .sort((a, b) => b.amountRupees - a.amountRupees);
}

/* ---------------------------------------------------------------- process */

export function getProcessGraph(): Promise<ProcessGraph> {
  // /process/map, not /process/graph: the map is the graph already collapsed
  // into the three semantic variant classes. /graph and /variants serve the
  // true per-sequence variants — there are thousands, which is exactly why
  // they are not what gets drawn.
  return get<ProcessGraph>('/process/map');
}

/* -------------------------------------------------------------- simulator */

interface ComponentList {
  projects: string[];
}

export function getSimulatorProjects(): Promise<string[]> {
  return get<ComponentList>('/simulate/components?limit=40').then((d) => d.projects);
}

export function runScenario(input: SimulatorInput): Promise<SimulatorOutput> {
  return post<SimulatorOutput>('/simulate', {
    sourceProject: input.sourceProject,
    destProject: input.destProject,
    engineerCount: input.engineerCount,
  });
}

/**
 * Previously enumerated every combination the fixture could answer. The
 * backend forecasts any pair it has observed delivery for, so there is no
 * finite list to guard against — an impossible scenario now comes back as a
 * 422 carrying the reason, which runScenario turns into a thrown Error.
 */
export function getAvailableScenarioInputs(): SimulatorInput[] {
  return [];
}

/* ------------------------------------------------------------------- meta */

export interface Meta {
  data_source: string;
  repos: string[];
  jira_projects: string[];
  k_anonymity_floor: number;
  k_anonymity_fallback: number;
  assumptions: Record<string, unknown>;
}

export function getMeta(): Promise<Meta> {
  return get<Meta>('/meta');
}

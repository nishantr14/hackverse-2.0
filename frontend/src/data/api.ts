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

// The four analytics fixtures are gone — those screens read the real backend
// now. This one stays until the workforce services exist; see the WORKFORCE
// section at the foot of this file.
import workforceFixture from '../mock-data/workforce.json';
import type {
  EmployeePreferences,
  EmployeeRecommendation,
  NamedSimulatorOutput,
  ProcessGraph,
  ProjectedImpact,
  Recommendation,
  SavePreferencesResult,
  Shift,
  SimulatorInput,
  SimulatorOutput,
  SpendRow,
  WasteRow,
  WasteType,
  Weekday,
  WorkforceFixture,
  WorkforceRecommendationSet,
  WorkforceRequirement,
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
 * The same scenario, asked to name people as well as price the move.
 *
 * Deliberately a separate function rather than a flag on `runScenario`: the
 * two return different amounts of personal data, and a caller should have to
 * say which one it wants. Capacity mode stays the default everywhere.
 */
export function runNamedScenario(
  input: SimulatorInput,
  opts: { shift?: Shift; availability?: Weekday[] } = {},
): Promise<NamedSimulatorOutput> {
  return post<NamedSimulatorOutput>('/simulate', {
    sourceProject: input.sourceProject,
    destProject: input.destProject,
    engineerCount: input.engineerCount,
    namedRecommendations: true,
    shift: opts.shift ?? 'flexible',
    availability: opts.availability ?? null,
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

/* ---------------------------------------------------------------------------
 * WORKFORCE
 *
 * Same swap-in contract as above, against services that do not exist yet:
 * resume ingestion, the RAG recommender, and the workforce side of the
 * simulator. Each function below names the endpoint it is standing in for, so
 * the backend team replaces one body and the screen does not change.
 *
 *   saveEmployeePreferences  ->  POST /workforce/preferences
 *   getWorkforceRequirement  ->  GET  /workforce/requirement
 *   getRecommendations       ->  POST /workforce/recommend
 *   getProjectedImpact       ->  POST /simulate
 *
 * NOTHING HERE READS THE ANALYTICS LAYER, and it must stay that way. The event
 * log is pseudonymised and cannot be attributed to a person; these responses
 * name people because the people volunteered the data. Joining the two would
 * turn an anonymous cost figure into a per-person one, which the product
 * states on screen that it does not do.
 * ------------------------------------------------------------------------- */

const workforce = workforceFixture as unknown as WorkforceFixture;

/**
 * Simulated latency for the workforce mocks only.
 *
 * The analytics endpoints have real network time now, so the shared helper
 * this replaces went with the fixtures. These four still resolve instantly
 * from a bundled JSON file, and a loading state that never renders is a
 * loading state nobody notices is broken when the real call is slow.
 */
function settle<T>(value: T, ms = 260): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/**
 * POST /workforce/preferences
 *
 * Kept local until that endpoint exists. It resolves rather than throwing so
 * the form is testable end to end now — but it returns `saved` from the
 * response rather than assuming success, so a real endpoint that rejects a
 * submission surfaces in the UI instead of being swallowed by an optimistic
 * "Saved".
 */
export function saveEmployeePreferences(
  prefs: EmployeePreferences,
): Promise<SavePreferencesResult> {
  // Referenced so the mock has the same shape of dependency the real call
  // will have, and so an empty submission is visible while developing.
  if (!prefs.employeeId) {
    return Promise.reject(new Error('An employee identifier is required to save preferences.'));
  }
  return settle({ saved: true, savedAt: new Date().toISOString() }, 600);
}

/** GET /workforce/requirement — the opening being staffed. */
export function getWorkforceRequirement(): Promise<WorkforceRequirement> {
  return settle(workforce.requirement);
}

/**
 * POST /workforce/recommend — real now, and no longer a fixture.
 *
 * The two hardcoded cards this used to return ("Employee A", "Employee C")
 * were the thing the feature existed to remove: a ranking that could not
 * respond to employee data because it was not computed from any. This ranks
 * consented profiles against a requirement derived from the component.
 */
export function getRecommendationSet(
  component: string,
  engineerCount: number,
  shift: Shift = 'flexible',
  availability?: Weekday[],
): Promise<WorkforceRecommendationSet> {
  return post<WorkforceRecommendationSet>('/workforce/recommend', {
    component,
    engineerCount,
    shift,
    availability: availability ?? null,
  });
}

/**
 * Adapter to the view model the shared card renders.
 *
 * ONE canonical wire type (`EmployeeRecommendation`), one card, two screens.
 * The card predates the backend and reads booleans where the engine now has
 * continuous sub-scores, so the thresholds are stated here rather than hidden
 * inside the component: "matched" means the dimension scored full marks, and
 * anything less shows as not met so a partial match is never rendered as a
 * clean tick.
 */
export function toRecommendation(rec: EmployeeRecommendation): Recommendation {
  return {
    employee: rec.name,
    match: rec.matchPercent,
    skills: rec.skills,
    preferenceMatch: rec.subScores.preferenceMatch >= 1,
    availabilityMatch: rec.subScores.availabilityMatch >= 1,
    reason: rec.reasons.join(' '),
    evidence: {
      resume: {
        projects: rec.evidence.resumeProjects,
        skills: rec.skills,
        experience: rec.evidence.resumeExperience,
      },
      preferences: {
        preferredShift: rec.evidence.declaredShift,
        workStyle: rec.evidence.workStyle,
        availability: rec.evidence.declaredAvailability,
      },
      requirement: {
        requiredSkills: rec.matchedSkills.concat(rec.missingSkills),
        requiredShift: rec.evidence.declaredShift,
        requiredAvailability: rec.evidence.declaredAvailability,
      },
      policies: [
        'A reallocation is proposed to the employee, never applied without their acknowledgement.',
        'Only employees who submitted a preference record can be named at all.',
      ],
    },
  };
}

/** Kept for the Workforce screen's existing call site. */
export async function getRecommendations(): Promise<Recommendation[]> {
  const req = workforce.requirement;
  const set = await getRecommendationSet(
    `apache/kafka/${req.component.toLowerCase()}`,
    req.engineersRequired,
    req.requiredShift,
    req.requiredAvailability,
  );
  return set.recommendedEmployees.concat(set.alternates).map(toRecommendation);
}

/**
 * POST /simulate — the same simulator the Simulator screen drives, asked a
 * workforce question instead of a project one. Returns deltas, never absolute
 * figures, so it can never be read as an observed measurement.
 */
export function getProjectedImpact(): Promise<ProjectedImpact> {
  return settle(workforce.impact, 500);
}

/**
 * THE SWAP-IN POINT.
 *
 * Every screen reads its data through these four functions and nothing else —
 * no component imports a JSON file directly. When the backend is up, replace
 * the body of each function with a fetch() and the UI does not change:
 *
 *   export async function getSpend(): Promise<SpendRow[]> {
 *     const res = await fetch(`${API_BASE}/spend`);
 *     if (!res.ok) throw new Error(`GET /spend failed: ${res.status}`);
 *     return res.json();
 *   }
 *
 * The functions are already async and already have a latency knob, so the
 * loading states in the UI are exercised now rather than discovered later.
 */

import processFixture from '../mock-data/process.json';
import simulatorFixture from '../mock-data/simulator.json';
import spendFixture from '../mock-data/spend.json';
import wasteFixture from '../mock-data/waste.json';
import workforceFixture from '../mock-data/workforce.json';
import type {
  EmployeePreferences,
  ProcessGraph,
  ProjectedImpact,
  Recommendation,
  SavePreferencesResult,
  SimulatorFixture,
  SimulatorInput,
  SimulatorOutput,
  SpendRow,
  WasteRow,
  WorkforceFixture,
  WorkforceRequirement,
} from './types';

/** Simulated network latency so loading states are real during development. */
const MOCK_LATENCY_MS = 260;

function settle<T>(value: T, ms = MOCK_LATENCY_MS): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export function getSpend(): Promise<SpendRow[]> {
  return settle(spendFixture as SpendRow[]);
}

export function getWaste(): Promise<WasteRow[]> {
  return settle(wasteFixture as WasteRow[]);
}

export function getProcessGraph(): Promise<ProcessGraph> {
  return settle(processFixture as ProcessGraph);
}

export function getSimulatorProjects(): Promise<string[]> {
  return settle((simulatorFixture as SimulatorFixture).projects);
}

/**
 * Looks the scenario up by its full input triple. The backend will run the
 * forecaster here instead; the signature and the thrown error stay the same,
 * which is why the UI must handle "no result for this combination" rather
 * than assume every combination resolves.
 */
export function runScenario(input: SimulatorInput): Promise<SimulatorOutput> {
  const { scenarios } = simulatorFixture as SimulatorFixture;
  const match = scenarios.find(
    (s) =>
      s.input.sourceProject === input.sourceProject &&
      s.input.destProject === input.destProject &&
      s.input.engineerCount === input.engineerCount,
  );

  if (!match) {
    return Promise.reject(
      new Error(
        `No forecast available for ${input.engineerCount} engineer(s) ${input.sourceProject} → ${input.destProject}.`,
      ),
    );
  }

  // Deliberately slower than the read endpoints: the simulator is doing work,
  // and the UI is specified to show that.
  return settle(match.output, 900);
}

/** Every combination the fixture can currently answer. Drives the UI's guards. */
export function getAvailableScenarioInputs(): SimulatorInput[] {
  return (simulatorFixture as SimulatorFixture).scenarios.map((s) => s.input);
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
 * POST /workforce/recommend
 *
 * The RAG call. Deliberately slower than the read endpoints for the same
 * reason `runScenario` is: retrieval plus generation is work, and the UI is
 * specified to show that it happened rather than appear to look it up.
 */
export function getRecommendations(): Promise<Recommendation[]> {
  return settle(workforce.recommendations, 900);
}

/**
 * POST /simulate — the same simulator the Simulator screen drives, asked a
 * workforce question instead of a project one. Returns deltas, never absolute
 * figures, so it can never be read as an observed measurement.
 */
export function getProjectedImpact(): Promise<ProjectedImpact> {
  return settle(workforce.impact, 500);
}

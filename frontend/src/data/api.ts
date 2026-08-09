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
import type {
  ProcessGraph,
  SimulatorFixture,
  SimulatorInput,
  SimulatorOutput,
  SpendRow,
  WasteRow,
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

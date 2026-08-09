/**
 * Frozen data contracts.
 *
 * These mirror exactly what the backend will emit. If a shape changes here it
 * has to change in the Postgres query layer too — treat this file as the
 * agreement between frontend and backend, not as frontend-local types.
 */

export interface SpendRow {
  workItem: string;
  project: string;
  component: string;
  authorHours: number;
  reviewHours: number;
  cost: number;
}

export type WasteType = 'rework' | 'latency' | 'meeting' | 'keyPerson';

export interface WasteRow {
  type: WasteType;
  project: string;
  component: string | null;
  amountRupees: number;
  detail: string;
}

export interface ProcessNode {
  id: string;
  label: string;
}

export interface ProcessEdge {
  from: string;
  to: string;
  frequency: number;
  costRupees: number;
  variant: string;
}

export interface VariantSummary {
  variant: string;
  shareOfWorkItems: number;
  shareOfCost: number;
}

export interface ProcessGraph {
  nodes: ProcessNode[];
  edges: ProcessEdge[];
  variantSummary: VariantSummary[];
}

export interface SimulatorInput {
  sourceProject: string;
  destProject: string;
  engineerCount: number;
}

export interface SimulatorOutput {
  /** Positive = the source project slips by this many weeks. */
  sourceDeltaWeeks: number;
  /** Negative = the destination project ships this many weeks earlier. */
  destDeltaWeeks: number;
  /**
   * Positive = the reallocation COSTS the organisation this much, net.
   *
   * Named for what it is. In the reference scenario the source project slips
   * five weeks while the destination gains three, so pricing both halves turns
   * an apparent win into a +₹8.2L bill — which is the whole argument for
   * modelling the project that loses people.
   */
  netCostRupees: number;
  /** P10–P90 style band, expressed as percent width. Band is primary. */
  confidenceLow: number;
  confidenceHigh: number;
  /** Optional single-number summary. Secondary to the band. */
  confidencePercent?: number;
  rampUpPenaltyApplied: boolean;
  rampUpNote?: string | null;
}

export interface SimulatorScenario {
  input: SimulatorInput;
  output: SimulatorOutput;
}

export interface SimulatorFixture {
  projects: string[];
  scenarios: SimulatorScenario[];
  notes?: Record<string, string>;
}

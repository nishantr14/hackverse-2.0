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
  /** Everything this work item cost: labour + CI + meetings + tokens. */
  cost: number;
  /**
   * The labour slice of `cost`, and the only part `authorHours` and
   * `reviewHours` account for. A blended hourly rate must divide THIS by
   * those hours — using the full `cost` folds meeting and token spend into
   * an engineer's rate and lands it above the staff band.
   */
  labourCost: number;
}

export type WasteType = 'rework' | 'latency' | 'meeting' | 'ci' | 'keyPerson';

export interface WasteRow {
  type: WasteType;
  project: string;
  component: string | null;
  /** Zero when `priced` is false. Never treat zero as "cost us nothing". */
  amountRupees: number;
  /**
   * False when the category is deliberately never converted to rupees.
   * Review latency is wall-clock waiting, which nobody is billed for —
   * pricing it at a salary rate is how a waste figure loses an argument.
   */
  priced: boolean;
  /** Duration behind the row. The primary readout when `priced` is false. */
  hours: number;
  /** How many underlying items the row aggregates. A "median" over one is not one. */
  nItems: number;
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

/**
 * What the map is NOT showing, so the filtering can be stated on screen
 * rather than hidden. 168 transitions drawn at once is a hairball in which
 * nothing is legible; the top 20 by cost carry ~81% of the money.
 */
export interface ProcessCoverage {
  transitionsShown: number;
  transitionsTotal: number;
  /** 0–1 share of total transition cost that the drawn edges represent. */
  costShare: number;
  meetingsExcluded: boolean;
  note: string | null;
}

export interface ProcessGraph {
  nodes: ProcessNode[];
  edges: ProcessEdge[];
  variantSummary: VariantSummary[];
  coverage?: ProcessCoverage;
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

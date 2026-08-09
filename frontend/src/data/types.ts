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

/* ---------------------------------------------------------------------------
 * WORKFORCE LAYER
 *
 * A SEPARATE LAYER FROM EVERYTHING ABOVE, AND THE SEPARATION IS THE POINT.
 *
 * Everything above this line is derived from the event log: observed,
 * pseudonymised at ingestion, never attributable to a person. Nothing here is.
 * This layer is built from what an employee VOLUNTEERED about themselves — a
 * preference form they filled in, and a resume they supplied — so it names
 * people, which the analytics layer must never do.
 *
 * The two must not be joined. `EmployeePreferences.employeeId` is a workforce
 * identifier and has nothing to do with `actor_hash`; there is deliberately no
 * type here that carries both, so a component cannot accidentally put an
 * observed cost figure next to a named person.
 *
 * What comes out is a RECOMMENDATION a human reviews, never an assignment.
 * ------------------------------------------------------------------------- */

export type Shift = 'morning' | 'afternoon' | 'evening' | 'flexible';
export type WorkArea = 'backend' | 'frontend' | 'data' | 'devops' | 'testing';
export type Weekday = 'mon' | 'tue' | 'wed' | 'thu' | 'fri';
export type WorkStyle = 'individual' | 'collaborative' | 'mixed';

/** What the employee chose to tell us. Every field is opt-in. */
export interface EmployeePreferences {
  employeeId: string;
  preferredShift: Shift;
  workAreas: WorkArea[];
  availability: Weekday[];
  workStyle: WorkStyle;
  openToOtherTeams: boolean;
}

export interface SavePreferencesResult {
  saved: boolean;
  /** ISO-8601, UTC. Rendered in local time, never stored that way. */
  savedAt: string;
}

/** The opening the manager is trying to fill. */
export interface WorkforceRequirement {
  project: string;
  component: string;
  engineersRequired: number;
  requiredSkills: string[];
  requiredShift: Shift;
  requiredAvailability: Weekday[];
}

/**
 * What the recommendation was grounded in — the retrieved context, shown so a
 * manager can see the reasoning rather than take a number on faith. Four
 * separate sources, kept separate on screen: a resume claim and a policy are
 * not the same kind of evidence and must not be presented as one.
 */
export interface RecommendationEvidence {
  resume: {
    projects: string[];
    skills: string[];
    experience: string[];
  };
  preferences: {
    preferredShift: Shift;
    workStyle: WorkStyle;
    availability: Weekday[];
  };
  requirement: {
    requiredSkills: string[];
    requiredShift: Shift;
    requiredAvailability: Weekday[];
  };
  policies: string[];
}

export interface Recommendation {
  /** A display name or identifier. No contact details, no band, no salary. */
  employee: string;
  /** 0–100. A fit score against one opening — not a rating of the person. */
  match: number;
  skills: string[];
  preferenceMatch: boolean;
  availabilityMatch: boolean;
  reason: string;
  evidence: RecommendationEvidence;
}

/**
 * Deltas the simulator projects if the recommendation were acted on.
 *
 * Signs follow the rest of the app: NEGATIVE is an improvement (time or cost
 * going down), so the UI reads the sign and never has to know which direction
 * is "good" for a given metric.
 */
export interface ProjectedImpact {
  cycleTimePct: number;
  reviewLatencyPct: number;
  costRupees: number;
  /** Named basis, so a projection can never be mistaken for an observation. */
  basis: string;
}

export interface WorkforceFixture {
  requirement: WorkforceRequirement;
  recommendations: Recommendation[];
  impact: ProjectedImpact;
}

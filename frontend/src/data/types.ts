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

/* ---------------------------------------------------------------------------
 * THE NAMED RECOMMENDATION CONTRACT
 *
 * What POST /workforce/recommend actually returns, and the single canonical
 * shape for a named candidate. Both the Workforce screen and the Simulator
 * read this; `Recommendation` above is now only the view model the shared
 * card renders, produced from this by `toRecommendation` in ./api.
 *
 * EVERY FIELD IS DECLARED OR RESUME-DERIVED. There is no cycle time,
 * throughput, review count, items merged or capability figure here, and there
 * must never be one: the analytics layer is pseudonymised and this layer names
 * people, so a performance number on a named card would be the join the
 * product states on screen that it does not make.
 * ------------------------------------------------------------------------- */

/** The five weighted dimensions. Adding a sixth is how a performance term
 *  would arrive, so the union is closed on purpose. */
export type FitDimension =
  | 'skillMatch'
  | 'experienceMatch'
  | 'preferenceMatch'
  | 'availabilityMatch'
  | 'projectFamiliarity';

export type FitBreakdown = Record<FitDimension, number>;

export interface EmployeeEvidence {
  resumeProjects: string[];
  resumeExperience: string[];
  declaredShift: Shift;
  declaredWorkAreas: WorkArea[];
  declaredAvailability: Weekday[];
  workStyle: WorkStyle;
  openToOtherTeams: boolean;
}

export interface EmployeeRecommendation {
  employeeId: string;
  name: string;
  /** 0–1. The weighted sum. `matchPercent` is the same number for display. */
  matchScore: number;
  matchPercent: number;
  subScores: FitBreakdown;
  /**
   * weight × sub-score per dimension. These sum to `matchScore`, which is why
   * they ARE the explanation rather than an approximation of one — there is
   * no model here to attribute, only arithmetic to show.
   */
  contributions: FitBreakdown;
  skills: string[];
  matchedSkills: string[];
  missingSkills: string[];
  reasons: string[];
  /** Honest caveats — a partial shift match, a missing day, an absent skill. */
  flags: string[];
  evidence: EmployeeEvidence;
}

export interface DerivedRequirement {
  project: string;
  component: string;
  engineersRequired: number;
  requiredSkills: string[];
  workAreas: WorkArea[];
  preferredShift: Shift;
  requiredAvailability: Weekday[];
  /** The component carried no skill signal; the UI must badge this. */
  thin: boolean;
  basis: string;
}

/** Why nobody should read these profiles as something a person submitted. */
export interface DataBasis {
  source: string;
  volunteered: boolean;
  label: string;
  note: string;
}

export interface ExcludedEmployee {
  employeeId: string;
  name: string;
  reason: string;
}

export interface WorkforceRecommendationSet {
  requirement: DerivedRequirement;
  recommendedEmployees: EmployeeRecommendation[];
  alternates: EmployeeRecommendation[];
  /** Excluded, not down-ranked: a stated boundary is not a low score. */
  excluded: ExcludedEmployee[];
  /**
   * People with no submitted preference record. Counted, never named — this
   * is what makes the consent gate visible instead of looking like a short
   * candidate list.
   */
  anonymousCapacity: { count: number; note: string };
  weights: FitBreakdown;
  explanationMethod: string;
  dataBasis: DataBasis;
  privacyBasis: string;
  humanInTheLoop: { decision: string; note: string; actions: string[] };
}

/** POST /simulate with `namedRecommendations: true`. Additive — every field of
 *  SimulatorOutput is unchanged, so capacity mode is byte-identical. */
export interface NamedSimulatorOutput extends SimulatorOutput {
  mode: 'capacity' | 'named';
  workforce?: WorkforceRecommendationSet;
}

import type {
  Candidate,
  Opening,
  RelocationAssumptions,
  RelocationFeasibility,
  SimulatorOutput,
  Verdict,
} from '../data/types';

/**
 * Reallocation economics.
 *
 * The simulator prices the DELIVERY half of a move — what the source project
 * loses and the destination gains — from the observed event log. It cannot
 * price the human half, because nothing in a GitHub event says what it costs
 * to move somebody to another city. So those four figures are typed in by the
 * director and carried through this file labelled as assumptions, all the way
 * to the screen. A number that came from a text box must never end up looking
 * like a number that came from the database.
 *
 * SIGN CONVENTION, one rule everywhere: a POSITIVE rupee figure is money the
 * organisation SPENDS. `SimulatorOutput.netCostRupees` already follows it, so
 * the assumptions are simply added to it and the net benefit is the negation
 * of the total. Nothing in here has to know which direction is "good".
 */

/** A 40-hour week, used only to turn ramp-up weeks into rupees. */
export const HOURS_PER_WEEK = 40;

/** What the form opens with — the figures in the brief, all editable. */
export const DEFAULT_ASSUMPTIONS: RelocationAssumptions = {
  relocationPackage: 100000,
  temporaryAccommodation: 50000,
  travel: 25000,
  extraRampUpWeeks: 3,
};

export const ASSUMPTION_FIELDS = [
  {
    key: 'relocationPackage' as const,
    label: 'Relocation package',
    hint: 'One-off, paid to the employee',
    kind: 'rupees' as const,
  },
  {
    key: 'temporaryAccommodation' as const,
    label: 'Temporary accommodation',
    hint: 'Bridging housing while they settle',
    kind: 'rupees' as const,
  },
  {
    key: 'travel' as const,
    label: 'Travel',
    hint: 'Moving costs and initial trips',
    kind: 'rupees' as const,
  },
  {
    key: 'extraRampUpWeeks' as const,
    label: 'Additional ramp-up',
    hint: 'On top of the ramp-up the simulator already applies',
    kind: 'weeks' as const,
  },
];

/**
 * Can this move even be offered?
 *
 * Deliberately NOT a term in the fit score. Declining to move is a personal
 * decision, and scoring somebody down for it would turn a preference form into
 * something that quietly penalises the people who answer it honestly. It is a
 * gate on the SCENARIO instead: a candidate who has not opted in is still
 * ranked on capability, and the move simply cannot be proposed.
 */
export function feasibility(candidate: Candidate, opening: Opening): RelocationFeasibility {
  if (candidate.currentLocation === opening.location) return 'not-required';
  if (!candidate.openToRelocation) return 'not-opted-in';
  return candidate.preferredLocations.includes(opening.location) ? 'preferred' : 'acceptable';
}

export const FEASIBILITY_LABEL: Record<RelocationFeasibility, string> = {
  'not-required': 'No relocation required',
  preferred: 'Relocation to a preferred location',
  acceptable: 'Open to relocation, not a stated preference',
  'not-opted-in': 'Has not opted in to relocation',
};

export const FEASIBILITY_TONE: Record<RelocationFeasibility, 'teal' | 'amber' | 'coral'> = {
  'not-required': 'teal',
  preferred: 'teal',
  acceptable: 'amber',
  'not-opted-in': 'coral',
};

/** Relocation costs do not apply to somebody who is already there. */
export function relocationApplies(f: RelocationFeasibility): boolean {
  return f !== 'not-required';
}

export interface CostLine {
  label: string;
  /** Positive = spent. */
  rupees: number;
  /** Rendered next to the figure so provenance is never in doubt. */
  provenance: 'simulated' | 'assumption' | 'derived';
  note: string;
}

export interface NetImpact {
  lines: CostLine[];
  /** Positive = the move costs money overall. */
  totalCostRupees: number;
  /** Positive = the move is worth doing on money alone. */
  netBenefitRupees: number;
  rampUpCostRupees: number;
  feasibility: RelocationFeasibility;
  verdict: Verdict;
  /** Plain-language reasons, in the order they should be read. */
  rationale: string[];
}

/**
 * Puts the simulated half and the assumed half together.
 *
 * `blendedHourly` is the observed blended labour rate from `/spend/summary`.
 * It is the only reason the ramp-up line can be called derived rather than
 * invented — but the 40-hour week it is multiplied by is still an assumption,
 * and the returned note says so.
 */
export function netImpact(
  candidate: Candidate,
  opening: Opening,
  sim: SimulatorOutput,
  assumptions: RelocationAssumptions,
  blendedHourly: number | null,
): NetImpact {
  const f = feasibility(candidate, opening);
  const applies = relocationApplies(f);

  const rampUpCostRupees =
    blendedHourly === null
      ? 0
      : Math.round(assumptions.extraRampUpWeeks * HOURS_PER_WEEK * blendedHourly);

  const lines: CostLine[] = [
    {
      label: 'Delivery impact, both projects',
      rupees: sim.netCostRupees,
      provenance: 'simulated',
      note: sim.priced
        ? 'Priced by the simulator from the event log — the source project losing capacity and the destination gaining it.'
        : // Never blank: an unpriced line with no explanation is worse than an
          // error, because it looks like a priced one that happens to be zero.
          (sim.priceNote ??
            'The simulator could not price this move — these components have no observed rate to price against.'),
    },
  ];

  if (applies) {
    lines.push(
      {
        label: 'Relocation package',
        rupees: assumptions.relocationPackage,
        provenance: 'assumption',
        note: 'Typed in for this scenario. Not an observed company cost.',
      },
      {
        label: 'Temporary accommodation',
        rupees: assumptions.temporaryAccommodation,
        provenance: 'assumption',
        note: 'Typed in for this scenario. Not an observed company cost.',
      },
      {
        label: 'Travel',
        rupees: assumptions.travel,
        provenance: 'assumption',
        note: 'Typed in for this scenario. Not an observed company cost.',
      },
    );
  }

  if (assumptions.extraRampUpWeeks > 0) {
    lines.push({
      label: `Additional ramp-up · ${assumptions.extraRampUpWeeks} week${
        assumptions.extraRampUpWeeks === 1 ? '' : 's'
      }`,
      rupees: rampUpCostRupees,
      provenance: blendedHourly === null ? 'assumption' : 'derived',
      note:
        blendedHourly === null
          ? 'Not priced — the blended labour rate could not be read from /spend/summary.'
          : `${assumptions.extraRampUpWeeks} weeks x ${HOURS_PER_WEEK}h x the observed blended labour rate. ` +
            'On top of the ramp-up the simulator already applied, never instead of it.',
    });
  }

  const totalCostRupees = lines.reduce((sum, l) => sum + l.rupees, 0);
  const netBenefitRupees = -totalCostRupees;

  const rationale: string[] = [];
  let verdict: Verdict;

  if (f === 'not-opted-in') {
    // The gate, and it outranks the arithmetic on purpose.
    verdict = 'do-not-proceed';
    rationale.push(
      `${candidate.employee} has not opted in to relocation, and this opening is in ${opening.location}. ` +
        'The move cannot be proposed regardless of what it would save.',
    );
    rationale.push(
      'This is not a mark against the candidate — their capability ranking is unchanged.',
    );
  } else {
    verdict = netBenefitRupees > 0 ? 'proceed' : 'do-not-proceed';

    rationale.push(
      netBenefitRupees > 0
        ? 'The delivery gain at the destination outweighs what the source project loses plus the cost of moving somebody.'
        : 'Once the source project’s slippage and the cost of the move are priced, this reallocation does not pay for itself.',
    );

    if (sim.sourceDeltaWeeks > 0) {
      rationale.push(
        `${opening.project} gains, but the source component slips ${sim.sourceDeltaWeeks.toFixed(
          1,
        )} weeks. That half is usually left out and it is the half that costs money.`,
      );
    }

    if (f === 'acceptable') {
      rationale.push(
        `${opening.location} is not somewhere ${candidate.employee} listed as preferred, so treat acceptance as uncertain.`,
      );
    }

    // Optional on the contract, so absence must not read as "narrow band".
    if (sim.confidencePercent !== undefined && sim.confidencePercent >= 40) {
      rationale.push(
        `Confidence band is wide (±${sim.confidencePercent}%). Treat the figure as a direction, not a budget.`,
      );
    }
  }

  return {
    lines,
    totalCostRupees,
    netBenefitRupees,
    rampUpCostRupees,
    feasibility: f,
    verdict,
    rationale,
  };
}

export type ProjectStatus = 'improved' | 'delayed' | 'not-modelled';

export interface ProjectOutcome {
  key: string;
  label: string;
  status: ProjectStatus;
  detail: string;
}

/**
 * The whole-system view.
 *
 * The simulator models exactly two components. Every other component is
 * reported as NOT MODELLED rather than "unchanged" — claiming a project is
 * unaffected because we did not look at it is the kind of quiet overreach this
 * product exists to argue against.
 */
export function systemOutcomes(
  candidate: Candidate,
  opening: Opening,
  sim: SimulatorOutput,
  otherComponents: string[],
): ProjectOutcome[] {
  const out: ProjectOutcome[] = [
    {
      key: opening.simulateKey,
      label: `${opening.project} / ${opening.component}`,
      status: sim.destDeltaWeeks < 0 ? 'improved' : 'delayed',
      detail:
        sim.destDeltaWeeks < 0
          ? `Ships ${Math.abs(sim.destDeltaWeeks).toFixed(1)} weeks earlier`
          : `No earlier delivery projected`,
    },
    {
      key: candidate.currentComponent,
      label: candidate.currentComponentLabel,
      status: sim.sourceDeltaWeeks > 0 ? 'delayed' : 'improved',
      detail:
        sim.sourceDeltaWeeks > 0
          ? `Slips ${sim.sourceDeltaWeeks.toFixed(1)} weeks`
          : 'No slippage projected',
    },
  ];

  for (const key of otherComponents.slice(0, 2)) {
    out.push({
      key,
      // "apache/kafka/streams" -> "apache/kafka / streams". Split on the LAST
      // separator: chained replace() hits the first one twice and produced
      // "apache / kafka/streams", which named the wrong thing.
      label: key.replace(/\/([^/]*)$/, ' / $1'),
      status: 'not-modelled',
      detail: 'Outside this scenario — not claimed to be unaffected',
    });
  }

  return out;
}

/** Sum of a candidate's fit terms. Shown so the score can be checked, not trusted. */
export function contributionTotal(candidate: Candidate): number {
  return candidate.contributions.reduce((sum, c) => sum + c.points, 0);
}

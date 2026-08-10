import type {
  EmployeeRecommendation,
  FitContribution,
  FitDimension,
  Shift,
  Weekday,
  WorkArea,
  WorkStyle,
} from '../data/types';

/**
 * Display labels and option lists for the workforce layer.
 *
 * One place, because the same vocabulary appears in the preference form, on
 * the recommendation cards and inside the evidence panel — three surfaces that
 * must not drift into calling the same thing by two names. The stored values
 * are the lowercase codes; these are only ever what a human reads.
 */

export const SHIFT_LABEL: Record<Shift, string> = {
  morning: 'Morning',
  afternoon: 'Afternoon',
  evening: 'Evening',
  flexible: 'Flexible',
};

export const WORK_AREA_LABEL: Record<WorkArea, string> = {
  backend: 'Backend',
  frontend: 'Frontend',
  data: 'Data',
  devops: 'DevOps',
  testing: 'Testing',
};

export const WEEKDAY_LABEL: Record<Weekday, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
};

export const WORK_STYLE_LABEL: Record<WorkStyle, string> = {
  individual: 'Individually',
  collaborative: 'Collaboratively',
  mixed: 'A mix of both',
};

/* ---------------------------------------------------------------------------
 * THE FIVE FIT DIMENSIONS
 *
 * The engine returns them keyed by `FitDimension`; the cards render them as
 * `FitContribution`, which carries a human label and — the part that matters —
 * a BASIS saying where the factor came from. That field is a truth claim, so
 * it is assigned once, here, per dimension rather than at each call site:
 *
 *   requirement  the opening asked for it (the required skills, derived from
 *                the component)
 *   volunteered  the employee stated it on the preference form
 *   assumption   we read it off a resume and took it at its word — nothing
 *                verifies years, project history or declared familiarity, and
 *                calling those "volunteered" would overstate what they are
 * ------------------------------------------------------------------------- */

export const FIT_DIMENSION_LABEL: Record<FitDimension, string> = {
  skillMatch: 'Required skills on the resume',
  experienceMatch: 'Relevant years and project history',
  preferenceMatch: 'Declared work areas and shift',
  availabilityMatch: 'Declared availability',
  projectFamiliarity: 'Declared familiarity with this component',
};

export const FIT_DIMENSION_BASIS: Record<FitDimension, FitContribution['basis']> = {
  skillMatch: 'requirement',
  experienceMatch: 'assumption',
  preferenceMatch: 'volunteered',
  availabilityMatch: 'volunteered',
  projectFamiliarity: 'assumption',
};

/**
 * The same five, short enough for a column heading.
 *
 * Used by the Simulator's recommendation card AND by the PDF export, so a
 * printed page and the screen it came from cannot name the same dimension two
 * different ways — which is the failure mode a document has and a screen does
 * not, because the document is read somewhere the screen isn't.
 */
export const FIT_DIMENSION_SHORT_LABEL: Record<FitDimension, string> = {
  skillMatch: 'Skills',
  experienceMatch: 'Experience',
  preferenceMatch: 'Preferences',
  availabilityMatch: 'Availability',
  projectFamiliarity: 'Familiarity',
};

/** Read order on the card: heaviest weight first, so the biggest term leads. */
export const FIT_DIMENSIONS: readonly FitDimension[] = [
  'skillMatch',
  'experienceMatch',
  'preferenceMatch',
  'availabilityMatch',
  'projectFamiliarity',
];

/**
 * The five weighted terms as whole percentage points that sum to `matchPercent`.
 *
 * LARGEST REMAINDER, NOT PER-TERM ROUNDING, AND IT LIVES HERE BECAUSE MORE
 * THAN ONE SURFACE RENDERS IT. Every consumer states, in words, that these
 * terms sum to the headline — the card prints "Sums to 87%" under it and the
 * PDF prints "sums to matchScore, so the arithmetic is the explanation". Five
 * independently rounded terms drift by up to two points, so a surface that
 * rounds its own way publishes a sentence its own numbers contradict. The PDF
 * did exactly that: 35 + 15.6 + 18.8 + 8 + 10 = 87.4 beside an 87% headline.
 *
 * Floor everything, then hand the leftover points to the largest fractional
 * parts until the total is exact.
 */
export function fitPoints(rec: EmployeeRecommendation): Record<FitDimension, number> {
  const terms = FIT_DIMENSIONS.map((d) => {
    const exact = rec.contributions[d] * 100;
    return { d, exact, points: Math.floor(exact) };
  });
  let short = rec.matchPercent - terms.reduce((sum, t) => sum + t.points, 0);

  for (const t of [...terms].sort((a, b) => b.exact - b.points - (a.exact - a.points))) {
    if (short <= 0) break;
    t.points += 1;
    short -= 1;
  }

  return Object.fromEntries(terms.map((t) => [t.d, t.points])) as Record<FitDimension, number>;
}

/** Turns a label map into the `{ value, label }` list the chip pickers take. */
function options<T extends string>(map: Record<T, string>): readonly { value: T; label: string }[] {
  return (Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));
}

export const SHIFT_OPTIONS = options(SHIFT_LABEL);
export const WORK_AREA_OPTIONS = options(WORK_AREA_LABEL);
export const WEEKDAY_OPTIONS = options(WEEKDAY_LABEL);
export const WORK_STYLE_OPTIONS = options(WORK_STYLE_LABEL);

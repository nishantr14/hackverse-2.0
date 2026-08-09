import type { Opening, WorkforceProfile } from '../data/types';
import { SHIFT_LABEL } from './workforce';

/**
 * The employee's own side of the match.
 *
 * Same openings the director sees, scored against the profile the employee
 * filled in themselves — and nothing else. No cost, no ranking against other
 * people, no productivity figure, because none of those are things an employee
 * should learn about themselves from a staffing tool, and two of them do not
 * exist in this layer at all.
 *
 * The rule is stated rather than learned, and the reasons handed back are the
 * terms that fired. An employee can read why an opening was surfaced and
 * disagree with it, which is the only version of this that is fair to them.
 */

export interface Opportunity {
  opening: Opening;
  /** 0–100, against this opening only. Never compared to another person. */
  fit: number;
  /** The terms that fired, in the order they should be read. */
  reasons: string[];
  /** Stated plainly so a low fit is not a mystery. */
  gaps: string[];
  relocationRequired: boolean;
}

/** Case- and punctuation-insensitive, so "CI/CD" and "ci-cd" are one skill. */
function normalise(skill: string): string {
  return skill.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function overlap(a: string[], b: string[]): string[] {
  const wanted = new Set(b.map(normalise));
  return a.filter((s) => wanted.has(normalise(s)));
}

export function matchOpportunities(
  profile: WorkforceProfile,
  openings: Opening[],
): Opportunity[] {
  return openings
    .map((opening) => score(profile, opening))
    .sort((a, b) => b.fit - a.fit);
}

function score(profile: WorkforceProfile, opening: Opening): Opportunity {
  const reasons: string[] = [];
  const gaps: string[] = [];
  let fit = 0;

  const matchedSkills = overlap(profile.skills, opening.requiredSkills);
  if (matchedSkills.length) {
    fit += Math.min(45, matchedSkills.length * 18);
    reasons.push(...matchedSkills);
  }

  const missing = opening.requiredSkills.filter(
    (s) => !profile.skills.some((p) => normalise(p) === normalise(s)),
  );
  if (missing.length) gaps.push(`Not on your profile: ${missing.join(', ')}`);

  if (profile.preferredComponents.includes(opening.simulateKey)) {
    fit += 15;
    reasons.push('A component you asked to work on');
  }

  const alreadyThere = profile.currentLocation === opening.location;
  const wouldRelocate =
    profile.openToRelocation && profile.preferredRelocationLocations.includes(opening.location);

  if (alreadyThere) {
    fit += 15;
    reasons.push(`Already in ${opening.location}`);
  } else if (wouldRelocate) {
    fit += 12;
    reasons.push(`${opening.location} is on your relocation list`);
  } else if (profile.openToRelocation) {
    fit += 4;
    gaps.push(`${opening.location} is not on your preferred relocation list`);
  } else {
    gaps.push(`Requires relocating to ${opening.location}, which you have not opted in to`);
  }

  if (
    profile.preferredShift === opening.requiredShift ||
    profile.preferredShift === 'flexible' ||
    opening.requiredShift === 'flexible'
  ) {
    fit += 13;
    reasons.push(`${SHIFT_LABEL[opening.requiredShift]} shift works with your preference`);
  } else {
    gaps.push(`Needs the ${SHIFT_LABEL[opening.requiredShift].toLowerCase()} shift`);
  }

  const daysCovered = opening.requiredAvailability.filter((d) => profile.availability.includes(d));
  if (daysCovered.length === opening.requiredAvailability.length) {
    fit += 12;
    reasons.push('Your availability covers the days required');
  } else if (daysCovered.length) {
    fit += 6;
    gaps.push(
      `Covers ${daysCovered.length} of ${opening.requiredAvailability.length} required days`,
    );
  } else {
    gaps.push('None of the required days are in your stated availability');
  }

  return {
    opening,
    fit: Math.max(0, Math.min(100, Math.round(fit))),
    reasons,
    gaps,
    relocationRequired: !alreadyThere,
  };
}

/** Below this an opening is shown as context rather than as a suggestion. */
export const OPPORTUNITY_THRESHOLD = 50;

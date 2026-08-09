import type { Candidate, Opening } from '../data/types';
import { FEASIBILITY_LABEL, feasibility } from '../lib/reallocation';
import { FIT_DIMENSION_LABEL } from '../lib/workforce';
import { GlassCard } from './GlassCard';

/**
 * Two or more candidates against ONE opening.
 *
 * NOT A LEADERBOARD, and the difference is structural rather than cosmetic.
 * Every column is a property of the fit between a person and this specific
 * requirement — capability for this component, compatibility with this
 * location, ramp-up into this work. Nothing in the table survives the opening
 * being changed, which is exactly what stops it becoming a ranking of people.
 *
 * Things that are deliberately not columns, and must never become columns:
 * cost, output, productivity, "best", or anything that would still make sense
 * with the opening removed. If a column would read as a fact about the person
 * rather than about the match, it does not belong here.
 */

const WORKLOAD_LABEL: Record<Candidate['currentWorkload'], string> = {
  light: 'Light',
  normal: 'Normal',
  heavy: 'Heavy',
};

/**
 * The skill term out of the fit breakdown.
 *
 * Looked up by the shared label constant rather than a string typed in here:
 * the label is written in one place and read in two, and a silent rename would
 * otherwise turn this row into a column of zeroes rather than an error.
 */
function skillPoints(c: Candidate): number {
  return c.contributions.find((x) => x.label === FIT_DIMENSION_LABEL.skillMatch)?.points ?? 0;
}

interface Row {
  label: string;
  render: (c: Candidate) => string;
  /** Highlights the strongest cell — only where "more" is unambiguously better. */
  best?: (c: Candidate) => number;
}

export function CandidateCompare({
  candidates,
  opening,
  rampUpWeeks,
}: {
  candidates: Candidate[];
  opening: Opening;
  rampUpWeeks: number;
}) {
  if (candidates.length < 2) return null;

  const rows: Row[] = [
    {
      label: 'Required skills matched',
      render: (c) => `${skillPoints(c)}%`,
      best: skillPoints,
    },
    {
      label: 'Relevant experience',
      render: (c) => `${c.experienceYears} yrs · ${c.primaryRole}`,
      best: (c) => c.experienceYears,
    },
    {
      label: 'Location compatibility',
      render: (c) =>
        c.currentLocation === opening.location
          ? `Already in ${opening.location}`
          : `${c.currentLocation} → ${opening.location}`,
    },
    {
      label: 'Relocation requirement',
      render: (c) => FEASIBILITY_LABEL[feasibility(c, opening)],
    },
    {
      label: 'Ramp-up estimate',
      render: (c) =>
        // Somebody already on a component of the same project has less to learn.
        c.currentComponent.split('/')[0] === opening.simulateKey.split('/')[0]
          ? `${rampUpWeeks} weeks`
          : `${rampUpWeeks + 2} weeks`,
    },
    {
      label: 'Current workload',
      render: (c) => WORKLOAD_LABEL[c.currentWorkload],
    },
    {
      label: 'Suitability for this opening',
      render: (c) => `${c.match}%`,
      best: (c) => c.match,
    },
  ];

  return (
    <GlassCard className="p-5" animate={false}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h3 className="text-[13px] font-semibold text-[var(--text-primary)]">
          Compared for this opening
        </h3>
        <p className="text-[11.5px] text-[var(--text-muted)]">
          {opening.project} / {opening.component}, {opening.location}
        </p>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[520px] border-collapse text-left">
          <thead>
            <tr>
              <th className="w-[190px] pb-2 text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
                Against this scenario
              </th>
              {candidates.map((c) => (
                <th
                  key={c.candidateId}
                  className="pb-2 text-[13px] font-semibold text-[var(--text-primary)]"
                >
                  {c.employee}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const top = row.best
                ? Math.max(...candidates.map((c) => row.best!(c)))
                : null;
              return (
                <tr key={row.label} className="border-t" style={{ borderColor: 'var(--border)' }}>
                  <th
                    scope="row"
                    className="py-2.5 pr-4 text-[12px] font-normal text-[var(--text-secondary)]"
                  >
                    {row.label}
                  </th>
                  {candidates.map((c) => {
                    const isTop = top !== null && row.best!(c) === top;
                    return (
                      <td
                        key={c.candidateId}
                        className="py-2.5 pr-4 text-[12.5px]"
                        style={{
                          color: isTop ? 'var(--text-primary)' : 'var(--text-secondary)',
                          fontWeight: isTop ? 600 : 400,
                        }}
                      >
                        {row.render(c)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
        Every column above is a property of the match to{' '}
        <strong className="text-[var(--text-secondary)]">this opening</strong>, not of the person.
        Change the opening and the table changes. There is no cost, output or productivity column,
        and there is no “best candidate” — the product evaluates suitability for a scenario, not
        an employee’s worth.
      </p>
    </GlassCard>
  );
}

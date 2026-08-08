import { motion } from 'framer-motion';
import { formatPercent } from '../lib/format';
import { EASE_GLASS } from '../lib/motion';
import { colorFor, type ProjectPalette } from '../lib/projectColors';
import type { ProjectSpend } from '../lib/spend';

/**
 * Authoring vs reviewing hours per project.
 *
 * Laid out as label-over-bar rather than as columns: this sits in a narrow
 * column beside the map, and a four-column row squeezes the bar — the one part
 * carrying the data — down to nothing.
 *
 * The bar wears its project's colour so a row here and a territory on the map
 * are visibly the same thing. Within a bar, authoring is the solid fill and
 * reviewing a wash of the same hue: parts of one measure, so one hue in two
 * strengths rather than two identities.
 */

interface HoursSplitProps {
  projects: ProjectSpend[];
  palette: ProjectPalette;
  focusProject: string | null;
  onFocusProject: (project: string | null) => void;
}

export function HoursSplit({ projects, palette, focusProject, onFocusProject }: HoursSplitProps) {
  const maxHours = Math.max(...projects.map((p) => p.authorHours + p.reviewHours), 1);
  const rows = [...projects].sort(
    (a, b) => b.authorHours + b.reviewHours - (a.authorHours + a.reviewHours),
  );

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11.5px] text-[var(--text-secondary)]">
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="block h-2.5 w-4 rounded-[2px]"
            style={{ background: 'rgb(168 178 194 / 0.9)' }}
          />
          Authoring
        </span>
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="block h-2.5 w-4 rounded-[2px]"
            style={{ background: 'rgb(168 178 194 / 0.3)' }}
          />
          Reviewing
        </span>
      </div>

      <ul className="mt-4 flex flex-col gap-1" onMouseLeave={() => onFocusProject(null)}>
        {rows.map((p, i) => {
          const total = p.authorHours + p.reviewHours;
          const scale = total / maxHours;
          const authorShare = p.authorHours / total;
          const reviewShare = p.reviewHours / total;
          const c = colorFor(palette, p.project);
          const dim = focusProject !== null && focusProject !== p.project;
          const focused = focusProject === p.project;

          return (
            <motion.li
              key={p.project}
              tabIndex={0}
              className="cursor-default rounded-lg px-2.5 py-2"
              animate={{
                opacity: dim ? 0.34 : 1,
                backgroundColor: focused ? `rgb(${c.rgb} / 0.1)` : `rgb(${c.rgb} / 0)`,
              }}
              transition={{ duration: 0.25, ease: EASE_GLASS }}
              onMouseEnter={() => onFocusProject(p.project)}
              onFocus={() => onFocusProject(p.project)}
              onBlur={() => onFocusProject(null)}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    aria-hidden
                    className="block h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ background: c.base }}
                  />
                  <span className="truncate text-[13px] font-medium text-[var(--text-primary)]">
                    {p.project}
                  </span>
                </span>
                <span className="tnum shrink-0 text-[12px] text-[var(--text-secondary)]">
                  {total} h ·{' '}
                  <span className="text-[var(--text-primary)]">{formatPercent(reviewShare, 0)}</span>{' '}
                  review
                </span>
              </div>

              <div className="relative mt-2 h-6">
                <motion.div
                  className="absolute inset-y-0 left-0 flex"
                  initial={{ width: 0, opacity: 0 }}
                  whileInView={{ width: `${scale * 100}%`, opacity: 1 }}
                  viewport={{ once: true, amount: 0.1, margin: '0px 0px -40px 0px' }}
                  transition={{ duration: 0.75, delay: 0.06 * i, ease: EASE_GLASS }}
                >
                  <span
                    className="flex h-full items-center overflow-hidden rounded-l-[4px]"
                    style={{ width: `${authorShare * 100}%`, background: c.base, marginRight: 2 }}
                  >
                    <span className="tnum truncate px-2 text-[11px] font-semibold text-[#0B0E14]">
                      {p.authorHours} h
                    </span>
                  </span>
                  <span
                    className="h-full rounded-r-[4px]"
                    style={{
                      width: `calc(${reviewShare * 100}% - 2px)`,
                      background: `rgb(${c.rgb} / 0.36)`,
                    }}
                  />
                </motion.div>
              </div>
            </motion.li>
          );
        })}
      </ul>
    </div>
  );
}

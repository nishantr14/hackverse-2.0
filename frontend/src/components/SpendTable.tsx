import type { SpendRow } from '../data/types';
import { formatPercent, formatRupeesExact } from '../lib/format';

/**
 * The table twin of the spend map. Required, not optional: every value the
 * chart encodes with area has to be readable without relying on area, colour
 * or a hover interaction.
 */

interface SpendTableProps {
  rows: SpendRow[];
  total: number;
}

export function SpendTable({ rows, total }: SpendTableProps) {
  const sorted = [...rows].sort((a, b) => b.cost - a.cost);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] border-collapse text-left">
        <caption className="sr-only">
          Priced work items, highest cost first. Cost equals observed engineer-hours multiplied by
          the role-band rate.
        </caption>
        <thead>
          <tr
            className="border-b text-[11px] tracking-[0.06em] text-[var(--text-secondary)] uppercase"
            style={{ borderColor: 'var(--border-strong)' }}
          >
            <th scope="col" className="py-2.5 pr-4 font-medium">
              Work item
            </th>
            <th scope="col" className="py-2.5 pr-4 font-medium">
              Project
            </th>
            <th scope="col" className="py-2.5 pr-4 font-medium">
              Component
            </th>
            <th scope="col" className="py-2.5 pr-4 text-right font-medium">
              Author h
            </th>
            <th scope="col" className="py-2.5 pr-4 text-right font-medium">
              Review h
            </th>
            <th scope="col" className="py-2.5 pr-4 text-right font-medium">
              Cost
            </th>
            <th scope="col" className="py-2.5 text-right font-medium">
              Share
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.workItem} className="border-b" style={{ borderColor: 'var(--border)' }}>
              <th
                scope="row"
                className="py-2.5 pr-4 text-[12.5px] font-medium text-[var(--text-primary)]"
              >
                {r.workItem}
              </th>
              <td className="py-2.5 pr-4 text-[12.5px] text-[var(--text-secondary)]">{r.project}</td>
              <td className="py-2.5 pr-4 text-[12.5px] text-[var(--text-secondary)]">
                {r.component}
              </td>
              <td className="tnum py-2.5 pr-4 text-right text-[12.5px] text-[var(--text-secondary)]">
                {r.authorHours}
              </td>
              <td className="tnum py-2.5 pr-4 text-right text-[12.5px] text-[var(--text-secondary)]">
                {r.reviewHours}
              </td>
              <td className="tnum py-2.5 pr-4 text-right text-[12.5px] text-[var(--text-primary)]">
                {formatRupeesExact(r.cost)}
              </td>
              <td className="tnum py-2.5 text-right text-[12.5px] text-[var(--text-secondary)]">
                {formatPercent(r.cost / total)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

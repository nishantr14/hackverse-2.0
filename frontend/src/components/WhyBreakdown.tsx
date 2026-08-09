import type { FitContribution } from '../data/types';

/**
 * The terms behind a fit score, drawn as a signed bar chart.
 *
 * HONESTY NOTE, AND IT IS THE WHOLE REASON THIS COMPONENT LOOKS THE WAY IT
 * DOES: these are not SHAP values. There is no trained model in the workforce
 * layer — no model means no Shapley attribution, and rendering made-up numbers
 * in the visual language of a real explainability method would be worse than
 * showing nothing, because it would be believed.
 *
 * What they are is the weighted terms of a stated rule over volunteered fields.
 * That rule is additive, so the terms genuinely do sum to the score, and the
 * component prints the sum so a reader can check rather than trust. If a real
 * attribution method is ever fitted, it fills the same `FitContribution` shape
 * and this component renders it unchanged — only the label at the bottom has
 * to change.
 */

const BASIS_LABEL: Record<FitContribution['basis'], string> = {
  volunteered: 'volunteered',
  requirement: 'from the opening',
  assumption: 'assumption',
};

export function WhyBreakdown({
  contributions,
  total,
}: {
  contributions: FitContribution[];
  total: number;
}) {
  // Scaled against the widest single term so the small ones stay visible;
  // scaling against the total would flatten everything below the top bar.
  const widest = Math.max(...contributions.map((c) => Math.abs(c.points)), 1);
  const sum = contributions.reduce((acc, c) => acc + c.points, 0);

  return (
    <div
      className="rounded-xl border p-5"
      style={{ borderColor: 'var(--border)', background: 'rgb(19 23 34 / 0.6)' }}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="text-[11px] tracking-[0.08em] text-[var(--text-secondary)] uppercase">
          Why this candidate?
        </p>
        <p className="text-[11.5px] text-[var(--text-muted)]">
          Percentage points contributed to the {total}% fit
        </p>
      </div>

      <ul className="mt-4 flex flex-col gap-2.5">
        {contributions.map((c) => {
          const positive = c.points >= 0;
          const width = (Math.abs(c.points) / widest) * 100;
          const color = positive ? 'var(--teal)' : 'var(--coral)';

          return (
            <li key={c.label} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[12.5px] text-[var(--text-primary)]">{c.label}</span>
                <span className="text-[11px] text-[var(--text-muted)]">
                  {BASIS_LABEL[c.basis]}
                </span>
              </div>
              <span className="tnum text-[12.5px] font-semibold" style={{ color }}>
                {positive ? '+' : '−'}
                {Math.abs(c.points)}%
              </span>

              {/* Split track: negatives grow left of centre, positives right,
                  so a penalty is legible as a penalty at a glance. */}
              <div className="col-span-2 grid grid-cols-2">
                <div className="flex justify-end">
                  {!positive && (
                    <span
                      className="block h-1.5 rounded-l-full"
                      style={{ width: `${width}%`, background: color, opacity: 0.85 }}
                    />
                  )}
                </div>
                <div>
                  {positive && (
                    <span
                      className="block h-1.5 rounded-r-full"
                      style={{ width: `${width}%`, background: color, opacity: 0.85 }}
                    />
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div
        className="mt-4 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-t pt-3"
        style={{ borderColor: 'var(--border)' }}
      >
        <span className="text-[12px] text-[var(--text-secondary)]">Sums to</span>
        <span className="tnum text-[13px] font-semibold text-[var(--text-primary)]">{sum}%</span>
      </div>

      <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--text-muted)]">
        These are the weighted terms of a stated rule over volunteered data —{' '}
        <strong className="text-[var(--text-secondary)]">not SHAP values</strong>. There is no
        trained model behind this layer, so there is no attribution to report; the terms are shown
        because the rule is additive and can be checked by hand.
      </p>
    </div>
  );
}

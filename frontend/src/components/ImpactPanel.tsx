import { formatWeekDelta } from '../lib/format';
import type { ConfidenceShape, Lane } from '../lib/simulator';
import { impactFraction, markerPct } from '../lib/simulator';

/**
 * One project's half of the trade.
 *
 * The panel fills like a tank as the result lands — coral for a project that
 * slips, teal for one that gains, height scaled to how many weeks are at
 * stake. The plan itself "rewrites": the resting marker slides from today's
 * date to the revised one, and the headline figure fades in from the plain
 * "no change yet" state it started in. Colour says which direction, the flood
 * height says how much, the marker says exactly where it landed.
 *
 * Deliberately built on plain CSS transitions rather than a JS-driven
 * animation loop: the target height/position is written to the DOM
 * synchronously the moment `revealed` flips, so the state is always correct
 * even if the tab cannot render the tween itself.
 */

const EASE_CSS = 'cubic-bezier(0.22, 1, 0.36, 1)';

interface ImpactPanelProps {
  lane: Lane;
  engineerCount: number;
  revealed: boolean;
  /** Null while idle — no forecast to feather the edge with yet. */
  conf: ConfidenceShape | null;
  /** The project's identity colour, matching its tile on the spend map. */
  identityColor: string;
}

export function ImpactPanel({ lane, engineerCount, revealed, conf, identityColor }: ImpactPanelProps) {
  const slips = lane.deltaWeeks > 0;
  const rgb = slips ? '240 101 79' : '45 212 191';
  const css = slips ? 'var(--coral)' : 'var(--teal)';
  const floodPct = revealed ? 14 + impactFraction(lane.deltaWeeks) * 72 : 0;
  const feather = conf ? conf.featherPct : 10;
  const marker = revealed ? markerPct(lane.deltaWeeks) : 50;

  return (
    <div
      className="relative overflow-hidden rounded-xl border"
      style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)' }}
    >
      {/* the flood */}
      <div
        aria-hidden
        className="absolute inset-x-0 bottom-0"
        style={{
          height: `${floodPct}%`,
          background: `linear-gradient(to top, rgb(${rgb} / 0.32) 0%, rgb(${rgb} / 0.32) ${100 - feather}%, rgb(${rgb} / 0) 100%)`,
          transition: `height 1.05s ${EASE_CSS}`,
        }}
      />

      <div className="relative z-10 flex min-h-[22rem] flex-col p-7">
        <span className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="block h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ background: identityColor }}
          />
          <span className="text-[16px] font-semibold text-[var(--text-primary)]">
            {lane.project}
          </span>
        </span>
        <span className="mt-1 text-[12px] text-[var(--text-secondary)]">
          {lane.role === 'source'
            ? `${engineerCount} engineer${engineerCount === 1 ? '' : 's'} out`
            : `${engineerCount} engineer${engineerCount === 1 ? '' : 's'} in`}
        </span>

        <div className="relative mt-auto" style={{ minHeight: 76 }}>
          <p
            className="absolute inset-0 flex items-end text-[13px] leading-relaxed text-[var(--text-secondary)]"
            style={{
              opacity: revealed ? 0 : 1,
              transition: `opacity 0.3s ${EASE_CSS}`,
            }}
          >
            Today&rsquo;s plan — no change yet.
          </p>

          <div
            className="absolute inset-0 flex flex-col justify-end"
            style={{
              opacity: revealed ? 1 : 0,
              transform: revealed ? 'translateY(0)' : 'translateY(10px)',
              transition: `opacity 0.5s ${EASE_CSS} 0.5s, transform 0.5s ${EASE_CSS} 0.5s`,
            }}
          >
            <span
              className="tnum flex items-center gap-2 text-[32px] leading-none font-semibold"
              style={{ color: css }}
            >
              <span aria-hidden>{slips ? '↓' : '↑'}</span>
              {formatWeekDelta(lane.deltaWeeks)}
            </span>
            <p className="mt-1.5 text-[12px] text-[var(--text-secondary)]">
              vs. today&rsquo;s committed plan
            </p>
          </div>
        </div>

        {/* the plan rewriting itself: today's date, sliding to the revised one */}
        <div className="mt-5">
          <div
            className="relative h-1.5 rounded-full"
            style={{ background: 'rgb(255 255 255 / 0.08)' }}
          >
            <span
              aria-hidden
              className="absolute top-1/2 left-1/2 h-2.5 w-px -translate-x-1/2 -translate-y-1/2"
              style={{ background: 'var(--border-strong)' }}
            />
            <div
              aria-hidden
              className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full"
              style={{
                left: `${marker}%`,
                background: revealed ? css : 'var(--text-secondary)',
                boxShadow: revealed ? `0 0 10px rgb(${rgb} / 0.7)` : 'none',
                transition: `left 1.05s ${EASE_CSS}, background 0.4s ${EASE_CSS}`,
              }}
            />
          </div>
          <div className="mt-1.5 flex justify-between text-[10.5px] text-[var(--text-secondary)]">
            <span>Earlier</span>
            <span>Today&rsquo;s plan</span>
            <span>Later</span>
          </div>
        </div>
      </div>
    </div>
  );
}

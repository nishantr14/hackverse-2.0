import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { IconSpend, IconWorkforce } from '../components/Icons';
import { EASE_GLASS, stagger } from '../lib/motion';
import { ROLE_HOME, ROLE_ROADMAP_NOTE, useRole } from '../lib/role';
import type { Role } from '../data/types';

/**
 * The way in.
 *
 * Deliberately NOT a login. There is no field to type a password into and no
 * pretence of one, because a fake credential box in a hackathon build teaches a
 * judge to distrust everything else on screen. It is a role switch, it says so,
 * and it says what the production control would be instead.
 *
 * Two cards rather than a dropdown because the two experiences really are
 * different products sharing a data layer — one is a person managing their own
 * record, the other is an executive making a staffing decision — and the entry
 * screen is the cheapest place to make that legible.
 */

const CARDS: {
  role: Role;
  title: string;
  blurb: string;
  cta: string;
  Icon: (props: { className?: string }) => React.ReactNode;
  points: string[];
}[] = [
  {
    role: 'employee',
    title: 'Employee',
    blurb: 'Manage your skills, preferences and mobility profile.',
    cta: 'Continue as Employee',
    Icon: IconWorkforce,
    points: [
      'Your own volunteered profile',
      'Openings you may be a fit for',
      'No spend, no ranking, no per-person metrics',
    ],
  },
  {
    role: 'director',
    title: 'VP / Director',
    blurb: 'Understand engineering spend, risk and workforce scenarios.',
    cta: 'Continue as Director',
    Icon: IconSpend,
    points: [
      'Process, spend, waste and the simulator',
      'Ranked candidates for an opening',
      'Reallocation priced end to end',
    ],
  },
];

export function Landing() {
  const { setRole } = useRole();
  const navigate = useNavigate();

  function choose(role: Role) {
    setRole(role);
    navigate(ROLE_HOME[role], { replace: true });
  }

  return (
    <div className="grain relative flex min-h-screen flex-col items-center justify-center px-6 py-16">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            'radial-gradient(70rem 40rem at 50% -12%, rgb(255 255 255 / 0.06), transparent 62%)',
        }}
      />

      <motion.div
        variants={stagger(0.09)}
        initial="hidden"
        animate="show"
        className="relative z-10 flex w-full max-w-[860px] flex-col items-center"
      >
        <motion.div
          variants={{
            hidden: { opacity: 0, y: 12 },
            show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE_GLASS } },
          }}
          className="flex flex-col items-center text-center"
        >
          <span
            className="grid h-11 w-11 place-items-center rounded-xl border"
            style={{
              borderColor: 'var(--border-strong)',
              background:
                'linear-gradient(140deg, rgb(255 255 255 / 0.14), rgb(255 255 255 / 0.02))',
            }}
            aria-hidden
          >
            <span className="block h-4 w-4 rounded-[4px] bg-[var(--ui)]" />
          </span>

          <h1 className="mt-6 text-[34px] leading-[1.1] font-semibold tracking-[-0.02em] text-[var(--text-primary)] sm:text-[42px]">
            Engineering Spend Intelligence
          </h1>
          <p className="mt-3 text-[15px] text-[var(--text-secondary)]">
            Turn software delivery data into decisions.
          </p>
        </motion.div>

        <motion.div
          variants={{
            hidden: { opacity: 0, y: 14 },
            show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE_GLASS } },
          }}
          className="mt-11 grid w-full gap-4 sm:grid-cols-2"
        >
          {CARDS.map(({ role, title, blurb, cta, Icon, points }) => (
            <button
              key={role}
              type="button"
              onClick={() => choose(role)}
              className="group flex flex-col rounded-2xl border p-6 text-left transition-colors"
              style={{
                borderColor: 'var(--border)',
                background: 'rgb(22 27 39 / 0.66)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--border-strong)';
                e.currentTarget.style.background = 'rgb(30 36 50 / 0.8)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--border)';
                e.currentTarget.style.background = 'rgb(22 27 39 / 0.66)';
              }}
            >
              <span
                className="grid h-9 w-9 place-items-center rounded-lg border text-[var(--text-secondary)]"
                style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)' }}
                aria-hidden
              >
                <Icon />
              </span>

              <h2 className="mt-4 text-[17px] font-semibold text-[var(--text-primary)]">{title}</h2>
              <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--text-secondary)]">
                {blurb}
              </p>

              <ul className="mt-4 flex flex-1 flex-col gap-1.5">
                {points.map((p) => (
                  <li
                    key={p}
                    className="flex gap-2 text-[12px] leading-relaxed text-[var(--text-muted)]"
                  >
                    <span aria-hidden style={{ color: 'var(--border-strong)' }}>
                      —
                    </span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>

              <span
                className="mt-6 inline-flex h-9 items-center justify-center rounded-lg border px-4 text-[13px] font-semibold transition-colors"
                style={{
                  color: 'var(--bg-page)',
                  background: 'var(--ui)',
                  borderColor: 'var(--ui)',
                }}
              >
                {cta}
              </span>
            </button>
          ))}
        </motion.div>

        {/* Says what this is, so nobody has to guess whether the demo has auth. */}
        <motion.p
          variants={{
            hidden: { opacity: 0 },
            show: { opacity: 1, transition: { duration: 0.5, ease: EASE_GLASS } },
          }}
          className="mt-9 max-w-[46rem] text-center text-[12px] leading-relaxed text-[var(--text-muted)]"
        >
          {ROLE_ROADMAP_NOTE} You can switch role at any time from the sidebar. The two experiences
          read the same volunteered workforce data; only the director’s reaches the event-log
          analytics, and no screen in either joins the two.
        </motion.p>
      </motion.div>
    </div>
  );
}

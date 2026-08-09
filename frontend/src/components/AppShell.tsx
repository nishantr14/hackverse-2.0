import { motion } from 'framer-motion';
import type { ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { snap } from '../lib/motion';
import {
  IconDatabase,
  IconLock,
  IconProcess,
  IconSimulator,
  IconSpend,
  IconWaste,
  IconWorkforce,
} from './Icons';

/**
 * One shell for every screen. The nav is numbered because the first four
 * screens are a sequence, not a set of tabs: what happened → where the money
 * went → what it cost us → what happens if we change it.
 *
 * Workforce sits below a divider and outside that numbering, because it is a
 * different kind of surface rather than a fifth step: the four above are built
 * on the pseudonymised event log, and Workforce is built on what an employee
 * volunteered. The sidebar's privacy note changes with it — see NOTE below.
 */

const NAV = [
  { to: '/process', step: '01', label: 'Process', Icon: IconProcess, hint: 'Discovered flow' },
  { to: '/spend', step: '02', label: 'Spend', Icon: IconSpend, hint: 'Where the money went' },
  { to: '/waste', step: '03', label: 'Waste & risk', Icon: IconWaste, hint: 'What it cost us' },
  { to: '/simulator', step: '04', label: 'Simulator', Icon: IconSimulator, hint: 'What happens next' },
] as const;

const WORKFORCE_NAV = {
  to: '/workforce',
  label: 'Workforce',
  Icon: IconWorkforce,
  hint: 'Who fits the work',
} as const;

/**
 * The footer note is per-route because a single global one would be false on
 * half the app. "No per-person view anywhere" is true of the analytics screens
 * and is the product's central privacy claim — but Workforce names people, so
 * printing that sentence there would be a visible contradiction. Each surface
 * states its own basis instead.
 */
const ANALYTICS_NOTE = {
  data: 'All figures computed from the event log.',
  privacy: 'Contributors pseudonymised at ingestion. No per-person view anywhere.',
};

const WORKFORCE_NOTE = {
  data: 'Volunteered by the employee — preferences and resume.',
  privacy: 'Never joined to the event log. Recommendations only; nobody is assigned.',
};

function Wordmark() {
  return (
    <div className="flex items-center gap-2.5 px-3">
      <span
        className="grid h-7 w-7 place-items-center rounded-lg border"
        style={{
          borderColor: 'var(--border-strong)',
          background: 'linear-gradient(140deg, rgb(255 255 255 / 0.14), rgb(255 255 255 / 0.02))',
        }}
        aria-hidden
      >
        <span className="block h-2.5 w-2.5 rounded-[3px] bg-[var(--ui)]" />
      </span>
      <span className="leading-tight">
        <span className="block text-[10px] tracking-[0.14em] text-[var(--text-secondary)] uppercase">
          Engineering
        </span>
        <span className="block text-[13px] font-semibold text-[var(--text-primary)]">
          Spend intelligence
        </span>
      </span>
    </div>
  );
}

function SidebarNote({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <p className="flex gap-2 px-3 text-[11px] leading-relaxed text-[var(--text-secondary)]">
      <span className="mt-px shrink-0 text-[var(--text-secondary)]">{icon}</span>
      <span>{children}</span>
    </p>
  );
}

function NavItem({
  to,
  label,
  hint,
  Icon,
  step,
  active,
}: {
  to: string;
  label: string;
  hint: string;
  Icon: (props: { className?: string }) => ReactNode;
  step?: string;
  active: boolean;
}) {
  return (
    <li>
      <NavLink
        to={to}
        className="relative flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors"
        style={{
          background: active ? 'var(--ui-active)' : 'transparent',
          color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        }}
      >
        {active && (
          <motion.span
            layoutId="nav-active"
            transition={snap}
            aria-hidden
            className="absolute top-2 bottom-2 -left-2 w-[2px] rounded-full bg-[var(--ui)]"
          />
        )}
        <Icon className="shrink-0" />
        <span className="flex-1 leading-tight">
          <span className="block text-[13px] font-medium">{label}</span>
          <span className="block text-[11px] text-[var(--text-secondary)]">{hint}</span>
        </span>
        {step && (
          <span className="tnum text-[10px] tracking-wider text-[var(--text-secondary)]">
            {step}
          </span>
        )}
      </NavLink>
    </li>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const note = pathname === WORKFORCE_NAV.to ? WORKFORCE_NOTE : ANALYTICS_NOTE;

  return (
    <div className="grain flex min-h-screen">
      {/* ambient wash — fixed, never scrolls, keeps the page from reading flat */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            'radial-gradient(70rem 40rem at 18% -10%, rgb(255 255 255 / 0.045), transparent 60%)',
        }}
      />

      <nav
        aria-label="Main"
        className="sticky top-0 z-10 flex h-screen w-[236px] shrink-0 flex-col justify-between overflow-y-auto border-r py-6"
        style={{ borderColor: 'var(--border)', background: 'rgb(11 14 20 / 0.6)' }}
      >
        <div className="flex flex-col gap-7">
          <Wordmark />

          <ul className="flex flex-col gap-0.5 px-2">
            {NAV.map((item) => (
              <NavItem key={item.to} {...item} active={pathname === item.to} />
            ))}
          </ul>

          <div className="flex flex-col gap-2">
            <div className="mx-3 border-t" style={{ borderColor: 'var(--border)' }} />
            <ul className="flex flex-col gap-0.5 px-2">
              <NavItem {...WORKFORCE_NAV} active={pathname === WORKFORCE_NAV.to} />
            </ul>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <div className="mx-3 border-t" style={{ borderColor: 'var(--border)' }} />
          <SidebarNote icon={<IconDatabase />}>{note.data}</SidebarNote>
          <SidebarNote icon={<IconLock />}>{note.privacy}</SidebarNote>
        </div>
      </nav>

      <main className="relative z-10 min-w-0 flex-1">{children}</main>
    </div>
  );
}

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
} from './Icons';

/**
 * One shell for all four screens. The nav is numbered because the four screens
 * are a sequence, not a set of tabs: what happened → where the money went →
 * what it cost us → what happens if we change it.
 */

const NAV = [
  { to: '/process', step: '01', label: 'Process', Icon: IconProcess, hint: 'Discovered flow' },
  { to: '/spend', step: '02', label: 'Spend', Icon: IconSpend, hint: 'Where the money went' },
  { to: '/waste', step: '03', label: 'Waste & risk', Icon: IconWaste, hint: 'What it cost us' },
  { to: '/simulator', step: '04', label: 'Simulator', Icon: IconSimulator, hint: 'What happens next' },
] as const;

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

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();

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
            {NAV.map(({ to, step, label, Icon, hint }) => {
              const active = pathname === to;
              return (
                <li key={to}>
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
                    <span className="tnum text-[10px] tracking-wider text-[var(--text-secondary)]">
                      {step}
                    </span>
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="flex flex-col gap-3">
          <div className="mx-3 border-t" style={{ borderColor: 'var(--border)' }} />
          <SidebarNote icon={<IconDatabase />}>
            All figures computed from the event log.
          </SidebarNote>
          <SidebarNote icon={<IconLock />}>
            Contributors pseudonymised at ingestion. No per-person view anywhere.
          </SidebarNote>
        </div>
      </nav>

      <main className="relative z-10 min-w-0 flex-1">{children}</main>
    </div>
  );
}

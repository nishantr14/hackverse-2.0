import type { ReactNode } from 'react';

/** Shared loading / error / not-yet-built surfaces. */

export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-lg ${className}`}
      style={{ background: 'rgb(94 107 128 / 0.10)' }}
    />
  );
}

export function LoadingPanel({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-4">
      <span className="sr-only">{label}</span>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[7.5rem]" />
        ))}
      </div>
      <Skeleton className="h-[30rem]" />
    </div>
  );
}

export function ErrorPanel({ error }: { error: Error }) {
  return (
    <div
      role="alert"
      className="rounded-xl border p-5"
      style={{ borderColor: 'rgb(240 101 79 / 0.35)', background: 'rgb(240 101 79 / 0.06)' }}
    >
      <p className="text-[13px] font-semibold" style={{ color: 'var(--coral)' }}>
        Could not load this view
      </p>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
        {error.message}
      </p>
    </div>
  );
}

/** Honest placeholder for the screens that are not built yet. */
export function ComingUp({ title, points }: { title: string; points: ReactNode[] }) {
  return (
    <div
      className="rounded-xl border border-dashed p-8"
      style={{ borderColor: 'var(--border-strong)', background: 'rgb(19 23 34 / 0.5)' }}
    >
      <p className="text-[13px] font-semibold text-[var(--text-primary)]">{title}</p>
      <ul className="mt-4 flex flex-col gap-2.5">
        {points.map((p, i) => (
          <li
            key={i}
            className="flex gap-3 text-[12.5px] leading-relaxed text-[var(--text-secondary)]"
          >
            <span aria-hidden className="mt-[7px] block h-1 w-1 shrink-0 rounded-full bg-[var(--border-strong)]" />
            <span>{p}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

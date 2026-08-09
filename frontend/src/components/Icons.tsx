/** Inline icons — 16px grid, 1.5 stroke. No icon dependency. */

type P = { className?: string };

const base = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

export const IconProcess = ({ className }: P) => (
  <svg {...base} className={className}>
    <circle cx="3.5" cy="3.5" r="2" />
    <circle cx="12.5" cy="8" r="2" />
    <circle cx="3.5" cy="12.5" r="2" />
    <path d="M5.2 4.6 10.8 7M10.8 9 5.2 11.4" />
  </svg>
);

export const IconSpend = ({ className }: P) => (
  <svg {...base} className={className}>
    <rect x="1.75" y="1.75" width="8" height="8" rx="1" />
    <rect x="11.25" y="1.75" width="3" height="4.5" rx="1" />
    <rect x="11.25" y="7.75" width="3" height="6.5" rx="1" />
    <rect x="1.75" y="11.25" width="8" height="3" rx="1" />
  </svg>
);

export const IconWaste = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 1.8 14.6 13.3H1.4L8 1.8Z" />
    <path d="M8 6.4v3.1M8 11.5h.01" />
  </svg>
);

export const IconSimulator = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M2 11.5 5.6 7l2.6 2.4L14 3.2" />
    <path d="M10.4 3.2H14v3.5" />
    <path d="M2 14.2h12" />
  </svg>
);

export const IconArrowUp = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 13V3.4M8 3.4 4.2 7.2M8 3.4l3.8 3.8" />
  </svg>
);

export const IconArrowDown = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 3v9.6M8 12.6 4.2 8.8M8 12.6l3.8-3.8" />
  </svg>
);

export const IconFlag = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M3.5 14V2.4h7.2l-1.4 2.8 1.4 2.8H3.5" />
  </svg>
);

export const IconLock = ({ className }: P) => (
  <svg {...base} className={className}>
    <rect x="3" y="7" width="10" height="7" rx="1.6" />
    <path d="M5.6 7V5.1a2.4 2.4 0 0 1 4.8 0V7" />
  </svg>
);

export const IconDownload = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M8 1.8v8.4M4.6 7.2 8 10.6l3.4-3.4" />
    <path d="M2 12.4v1.2c0 .55.45 1 1 1h10c.55 0 1-.45 1-1v-1.2" />
  </svg>
);

export const IconWorkforce = ({ className }: P) => (
  <svg {...base} className={className}>
    <circle cx="6" cy="5.2" r="2.4" />
    <path d="M1.9 14.2c0-2.3 1.8-4 4.1-4s4.1 1.7 4.1 4" />
    <path d="M11 3.2a2.4 2.4 0 0 1 0 4.5" />
    <path d="M12.4 10.6c1.1.6 1.8 1.9 1.8 3.6" />
  </svg>
);

export const IconCheck = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M3 8.4 6.4 11.8 13 5.2" />
  </svg>
);

export const IconChevron = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M4.4 6.2 8 9.8l3.6-3.6" />
  </svg>
);

export const IconDatabase = ({ className }: P) => (
  <svg {...base} className={className}>
    <ellipse cx="8" cy="3.6" rx="5" ry="1.9" />
    <path d="M3 3.6v8.8c0 1 2.2 1.9 5 1.9s5-.9 5-1.9V3.6" />
    <path d="M3 8c0 1 2.2 1.9 5 1.9s5-.9 5-1.9" />
  </svg>
);

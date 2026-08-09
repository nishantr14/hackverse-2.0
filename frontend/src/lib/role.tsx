import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { Role } from '../data/types';

/**
 * Which experience the visitor is in.
 *
 * THIS IS A DEMO ROLE SELECTOR, NOT AUTHENTICATION. There is no password, no
 * token, no session and no server-side check, and the app says so on the
 * landing screen rather than implying otherwise. Anyone can switch role from
 * the sidebar; the value lives in localStorage and nowhere else.
 *
 * What it IS good for is proving the separation is real in the UI: an employee
 * has no route to the spend, waste or simulator screens, and the router
 * enforces that rather than merely hiding the links. In production the same
 * boundary would be drawn by enterprise SSO plus RBAC, with the permission
 * check on the server where it cannot be edited by the client — see
 * `ROLE_ROADMAP_NOTE`, which is rendered on screen so nobody mistakes the
 * prototype for the real control.
 */

const STORAGE_KEY = 'esi.role';

export const ROLE_ROADMAP_NOTE =
  'Prototype role selection. Enterprise SSO and RBAC would enforce these permissions server-side.';

export const ROLE_LABEL: Record<Role, string> = {
  employee: 'Employee',
  director: 'VP / Director',
};

/** Where each role lands, and the first thing the router falls back to. */
export const ROLE_HOME: Record<Role, string> = {
  employee: '/me/profile',
  director: '/process',
};

/**
 * Routes an employee may reach. Anything not listed is director-only, so a new
 * analytics screen is private by default rather than public by omission.
 */
export const EMPLOYEE_ROUTES = ['/me/profile', '/me/opportunities'] as const;

export function isEmployeeRoute(pathname: string): boolean {
  return EMPLOYEE_ROUTES.some((r) => pathname === r || pathname.startsWith(`${r}/`));
}

function readStoredRole(): Role | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === 'employee' || raw === 'director' ? raw : null;
  } catch {
    // Private browsing, disabled storage, embedded webview. Not being able to
    // remember the choice is fine; crashing the app over it is not.
    return null;
  }
}

interface RoleContextValue {
  role: Role | null;
  setRole: (role: Role) => void;
  clearRole: () => void;
}

const RoleContext = createContext<RoleContextValue | null>(null);

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role | null>(readStoredRole);

  const setRole = useCallback((next: Role) => {
    setRoleState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* the choice still applies for this session */
    }
  }, []);

  const clearRole = useCallback(() => {
    setRoleState(null);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* nothing to clean up */
    }
  }, []);

  const value = useMemo(() => ({ role, setRole, clearRole }), [role, setRole, clearRole]);

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole(): RoleContextValue {
  const ctx = useContext(RoleContext);
  if (!ctx) throw new Error('useRole must be used inside a RoleProvider');
  return ctx;
}

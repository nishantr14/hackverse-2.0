import type { ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import type { Role } from './data/types';
import { ROLE_HOME, RoleProvider, useRole } from './lib/role';
import { EmployeeOpportunities } from './screens/EmployeeOpportunities';
import { EmployeeProfile } from './screens/EmployeeProfile';
import { Landing } from './screens/Landing';
import { ProcessView } from './screens/ProcessView';
import { SimulatorView } from './screens/SimulatorView';
import { SpendView } from './screens/SpendView';
import { WasteView } from './screens/WasteView';
import { Workforce } from './screens/Workforce';

/**
 * ROUTING IS THE PERMISSION BOUNDARY, not the sidebar.
 *
 * Hiding a nav link is a cosmetic separation — anyone who types the URL walks
 * straight in, and a demo that claims role separation while doing only that
 * would be claiming something false. `RequireRole` redirects instead, so an
 * employee who navigates to /spend lands back on their own profile.
 *
 * It is still a prototype control: the role lives in localStorage and the
 * visitor can change it. What it demonstrates is that the SHAPE of the
 * permission model is real and enforced in one place, which is where enterprise
 * SSO and RBAC would slot in — see lib/role.tsx.
 */

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { role: current } = useRole();

  // No role chosen yet: back to the entry screen rather than a guess.
  if (!current) return <Navigate to="/" replace />;
  // Wrong role: to their own home, never a dead end or a blank screen.
  if (current !== role) return <Navigate to={ROLE_HOME[current]} replace />;

  return <>{children}</>;
}

function Employee({ children }: { children: ReactNode }) {
  return <RequireRole role="employee">{children}</RequireRole>;
}

function Director({ children }: { children: ReactNode }) {
  return <RequireRole role="director">{children}</RequireRole>;
}

export default function App() {
  return (
    <RoleProvider>
      <Routes>
        {/* The way in, and the way back when switching role. Outside the shell:
            it has no nav, because at that point there is nothing to navigate. */}
        <Route path="/" element={<Landing />} />

        <Route
          path="*"
          element={
            <AppShell>
              <Routes>
                {/* --- employee --- */}
                <Route
                  path="/me/profile"
                  element={
                    <Employee>
                      <EmployeeProfile />
                    </Employee>
                  }
                />
                <Route
                  path="/me/opportunities"
                  element={
                    <Employee>
                      <EmployeeOpportunities />
                    </Employee>
                  }
                />

                {/* --- director: the analytics sequence, unchanged --- */}
                <Route
                  path="/process"
                  element={
                    <Director>
                      <ProcessView />
                    </Director>
                  }
                />
                <Route
                  path="/spend"
                  element={
                    <Director>
                      <SpendView />
                    </Director>
                  }
                />
                <Route
                  path="/waste"
                  element={
                    <Director>
                      <WasteView />
                    </Director>
                  }
                />
                <Route
                  path="/simulator"
                  element={
                    <Director>
                      <SimulatorView />
                    </Director>
                  }
                />
                {/* Workforce sits after the four analytics screens because it is
                    a different kind of surface, not a fifth step of the same
                    argument: it reads volunteered data, and it is the only
                    screen that names anyone. See screens/Workforce.tsx. */}
                <Route
                  path="/workforce"
                  element={
                    <Director>
                      <Workforce />
                    </Director>
                  }
                />

                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          }
        />
      </Routes>
    </RoleProvider>
  );
}

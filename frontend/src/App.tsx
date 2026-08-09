import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { ProcessView } from './screens/ProcessView';
import { SimulatorView } from './screens/SimulatorView';
import { SpendView } from './screens/SpendView';
import { WasteView } from './screens/WasteView';
import { Workforce } from './screens/Workforce';

export default function App() {
  return (
    <AppShell>
      <Routes>
        {/* Lands on the process view: that is the order the demo narrates in —
            how work moves, what it cost, what was wasted, what to change. */}
        <Route path="/" element={<Navigate to="/process" replace />} />
        <Route path="/process" element={<ProcessView />} />
        <Route path="/spend" element={<SpendView />} />
        <Route path="/waste" element={<WasteView />} />
        <Route path="/simulator" element={<SimulatorView />} />
        {/* Workforce sits after the four analytics screens because it is a
            different kind of surface, not a fifth step of the same argument:
            it reads volunteered data, and it is the only screen that names
            anyone. See screens/Workforce.tsx. */}
        <Route path="/workforce" element={<Workforce />} />
        <Route path="*" element={<Navigate to="/spend" replace />} />
      </Routes>
    </AppShell>
  );
}

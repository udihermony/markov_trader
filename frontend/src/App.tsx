import { Outlet, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Layout } from './components/Layout'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { TodayPage } from './pages/TodayPage'
import { WalletsPage } from './pages/WalletsPage'
import { WalletDetailPage } from './pages/WalletDetailPage'
import { CreateWalletPage } from './pages/CreateWalletPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { PresetPickerPage } from './pages/PresetPickerPage'
import { StrategyBuilderPage } from './pages/StrategyBuilderPage'
import { LabPage } from './pages/LabPage'
import { SettingsPage } from './pages/SettingsPage'

// A single shared parent route element, not one Layout instance per page
// (each individually wrapping itself in <protect(...)>) — the previous
// per-route wrapping remounted Layout (and the copilot panel inside it) on
// every navigation, silently starting a new chat conversation each time.
function ProtectedLayout() {
  return (
    <ProtectedRoute>
      <Layout>
        <Outlet />
      </Layout>
    </ProtectedRoute>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<TodayPage />} />
        <Route path="/wallets" element={<WalletsPage />} />
        <Route path="/wallets/new" element={<CreateWalletPage />} />
        <Route path="/wallets/:id" element={<WalletDetailPage />} />
        <Route path="/strategies" element={<StrategiesPage />} />
        <Route path="/strategies/new" element={<PresetPickerPage />} />
        <Route path="/strategies/new/build" element={<StrategyBuilderPage />} />
        <Route path="/strategies/:id/edit" element={<StrategyBuilderPage />} />
        <Route path="/lab" element={<LabPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  )
}

export default App

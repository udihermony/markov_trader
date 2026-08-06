import type { ReactNode } from 'react'
import { Route, Routes } from 'react-router-dom'
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

function protect(children: ReactNode) {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/" element={protect(<TodayPage />)} />
      <Route path="/wallets" element={protect(<WalletsPage />)} />
      <Route path="/wallets/new" element={protect(<CreateWalletPage />)} />
      <Route path="/wallets/:id" element={protect(<WalletDetailPage />)} />
      <Route path="/strategies" element={protect(<StrategiesPage />)} />
      <Route path="/strategies/new" element={protect(<PresetPickerPage />)} />
      <Route path="/strategies/new/build" element={protect(<StrategyBuilderPage />)} />
      <Route path="/strategies/:id/edit" element={protect(<StrategyBuilderPage />)} />
    </Routes>
  )
}

export default App

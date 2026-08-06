import { Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Layout } from './components/Layout'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { TodayPage } from './pages/TodayPage'
import { WalletsPage } from './pages/WalletsPage'
import { WalletDetailPage } from './pages/WalletDetailPage'
import { CreateWalletPage } from './pages/CreateWalletPage'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Layout>
              <TodayPage />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/wallets"
        element={
          <ProtectedRoute>
            <Layout>
              <WalletsPage />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/wallets/new"
        element={
          <ProtectedRoute>
            <Layout>
              <CreateWalletPage />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/wallets/:id"
        element={
          <ProtectedRoute>
            <Layout>
              <WalletDetailPage />
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

export default App

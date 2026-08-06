import { createContext, useContext, useState, type ReactNode } from 'react'
import { api, clearToken, getToken, setToken } from './api'

interface TokenResponse {
  access_token: string
  token_type: string
}

interface AuthContextValue {
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => getToken() !== null)

  async function login(email: string, password: string) {
    const res = await api.post<TokenResponse>('/auth/login', { email, password })
    setToken(res.access_token)
    setIsAuthenticated(true)
  }

  async function register(email: string, password: string) {
    await api.post('/auth/register', { email, password })
    await login(email, password)
  }

  function logout() {
    clearToken()
    setIsAuthenticated(false)
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

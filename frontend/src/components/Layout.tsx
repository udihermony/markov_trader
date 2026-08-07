import { useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { Button } from './ui/Button'
import { CopilotPanel } from './copilot/CopilotPanel'

function NavItem({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        `px-3 py-1.5 rounded-md text-sm font-medium ${
          isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
        }`
      }
    >
      {children}
    </NavLink>
  )
}

export function Layout({ children }: { children: ReactNode }) {
  const { logout } = useAuth()
  const [copilotOpen, setCopilotOpen] = useState(false)

  return (
    <div className="h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white shrink-0">
        <div className="px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <span className="font-semibold text-slate-800">Markov Trader</span>
            <nav className="flex gap-1">
              <NavItem to="/">Today</NavItem>
              <NavItem to="/wallets">Wallets</NavItem>
              <NavItem to="/strategies">Strategies</NavItem>
              <NavItem to="/lab">Lab</NavItem>
              <NavItem to="/settings">Settings</NavItem>
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <Button variant={copilotOpen ? 'primary' : 'secondary'} onClick={() => setCopilotOpen((o) => !o)}>
              Copilot
            </Button>
            <Button variant="ghost" onClick={logout}>
              Log out
            </Button>
          </div>
        </div>
      </header>
      <div className="flex-1 flex min-h-0">
        <main className="flex-1 min-w-0 overflow-y-auto px-4 py-6">
          <div className="max-w-4xl mx-auto">{children}</div>
        </main>
        <CopilotPanel open={copilotOpen} />
      </div>
    </div>
  )
}

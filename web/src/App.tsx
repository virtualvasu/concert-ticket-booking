import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { cn } from '@/lib/utils'
import { AdminPage } from '@/pages/AdminPage'
import { BuyerPage } from '@/pages/BuyerPage'

const link = ({ isActive }: { isActive: boolean }) =>
  cn('text-sm font-medium transition-colors hover:text-foreground', isActive ? 'text-foreground' : 'text-muted-foreground')

export default function App() {
  return (
    <BrowserRouter>
      <header className="border-b">
        <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <span className="font-semibold">Concert tickets</span>
          <div className="flex gap-4">
            <NavLink to="/" end className={link}>
              Buy
            </NavLink>
            <NavLink to="/admin" className={link}>
              Admin
            </NavLink>
          </div>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<BuyerPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Routes>
      </main>
      <Toaster richColors />
    </BrowserRouter>
  )
}

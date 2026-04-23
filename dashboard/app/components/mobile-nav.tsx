'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Bot, LayoutDashboard, Menu, Moon, ScrollText, Settings, Sun, X } from 'lucide-react'
import { useTheme } from 'next-themes'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

export function MobileNav() {
  const [open, setOpen] = useState(false)
  const pathname = usePathname()
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])
  useEffect(() => setOpen(false), [pathname])
  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [open])

  const links = [
    { href: '/', icon: <LayoutDashboard className="size-4" />, label: 'Overview' },
    { href: '/settings', icon: <Settings className="size-4" />, label: 'Settings' },
    { href: '/logs', icon: <ScrollText className="size-4" />, label: 'Logs' },
  ]

  return (
    <>
      {/* Top bar — mobile only */}
      <header className="flex items-center justify-between border-b bg-card px-4 py-2.5 md:hidden">
        <div className="flex items-center gap-2.5">
          <div className="flex size-7 items-center justify-center rounded-md bg-primary/10">
            <Bot className="size-4 text-primary" />
          </div>
          <span className="text-sm font-semibold">Claude Trader</span>
        </div>
        <div className="flex items-center gap-1">
          {mounted && (
            <Button
              variant="ghost" size="sm" className="size-8 p-0"
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
            >
              {resolvedTheme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          )}
          <Button variant="ghost" size="sm" className="size-8 p-0" onClick={() => setOpen(true)}>
            <Menu className="size-4" />
          </Button>
        </div>
      </header>

      {/* Backdrop */}
      <div
        aria-hidden
        className={cn(
          'fixed inset-0 z-40 bg-black/60 transition-opacity duration-200 md:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={() => setOpen(false)}
      />

      {/* Slide-in drawer */}
      <div className={cn(
        'fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r bg-card shadow-2xl',
        'transition-transform duration-200 ease-in-out md:hidden',
        open ? 'translate-x-0' : '-translate-x-full',
      )}>
        <div className="flex items-center justify-between p-4">
          <div className="flex items-center gap-2.5">
            <div className="flex size-7 items-center justify-center rounded-md bg-primary/10">
              <Bot className="size-4 text-primary" />
            </div>
            <span className="text-sm font-semibold">Claude Trader</span>
          </div>
          <Button variant="ghost" size="sm" className="size-8 p-0" onClick={() => setOpen(false)}>
            <X className="size-4" />
          </Button>
        </div>

        <Separator />

        <nav className="flex flex-col gap-0.5 p-2">
          {links.map(({ href, icon, label }) => {
            const active = pathname === href || (href !== '/' && pathname.startsWith(href))
            return (
              <Link key={href} href={href} onClick={() => setOpen(false)}>
                <div className={cn(
                  'flex items-center gap-2 rounded-md px-3 py-2.5 text-sm font-medium transition-colors',
                  active
                    ? 'bg-secondary text-secondary-foreground'
                    : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                )}>
                  {icon}
                  {label}
                </div>
              </Link>
            )
          })}
        </nav>

        <div className="mt-auto p-4">
          {mounted && (
            <Button
              variant="outline" size="sm" className="w-full justify-start gap-2"
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
            >
              {resolvedTheme === 'dark'
                ? <><Sun className="size-4" /> Light mode</>
                : <><Moon className="size-4" /> Dark mode</>}
            </Button>
          )}
        </div>
      </div>
    </>
  )
}

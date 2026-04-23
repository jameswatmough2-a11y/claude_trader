'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Activity, Bot, LayoutDashboard, RefreshCw, Settings, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Health {
  status: string
  paper_trading: boolean
  tracked_symbols: string[]
  model: string
}

interface LatestDecision {
  snapshot_time: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtCycleTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function NavLink({
  href,
  icon,
  children,
}: {
  href: string
  icon: React.ReactNode
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const active = pathname === href || (href !== '/' && pathname.startsWith(href))
  return (
    <Link href={href}>
      <div className={cn(
        'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors',
        active
          ? 'bg-secondary text-secondary-foreground'
          : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
      )}>
        {icon}
        {children}
      </div>
    </Link>
  )
}

// ── Component ─────────────────────────────────────────────────────────────────

export function AppSidebar() {
  const [health, setHealth] = useState<Health | null>(null)
  const [online, setOnline] = useState<boolean | null>(null)
  const [lastCycle, setLastCycle] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const [h, decisions]: [Health, LatestDecision[]] = await Promise.all([
          fetch('/api/bot/health').then(r => { if (!r.ok) throw new Error(); return r.json() }),
          fetch('/api/bot/decisions?limit=1').then(r => r.json()),
        ])
        setHealth(h)
        setOnline(true)
        if (decisions.length > 0) setLastCycle(decisions[0].snapshot_time)
      } catch {
        setOnline(false)
        setHealth(null)
      }
    }

    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r bg-card">
      {/* ── Brand ──────────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2.5">
          <div className="flex size-7 items-center justify-center rounded-md bg-primary/10">
            <Bot className="size-4 text-primary" />
          </div>
          <span className="text-sm font-semibold tracking-tight">Claude Trader</span>
        </div>

        {health ? (
          <Badge
            variant={health.paper_trading ? 'outline' : 'default'}
            className="w-fit text-xs"
          >
            {health.paper_trading ? 'Paper Trading' : 'Live Trading'}
          </Badge>
        ) : (
          <Badge variant="outline" className="w-fit text-xs text-muted-foreground">
            Loading…
          </Badge>
        )}
      </div>

      <Separator />

      {/* ── Navigation ─────────────────────────────────────────────────── */}
      <nav className="flex flex-col gap-0.5 p-2">
        <NavLink href="/" icon={<LayoutDashboard className="size-4" />}>
          Overview
        </NavLink>
        <NavLink href="/settings" icon={<Settings className="size-4" />}>
          Settings
        </NavLink>
      </nav>

      <Separator />

      {/* ── Bot status ─────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-3.5 p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Status
        </p>

        <div className="flex items-center gap-2">
          {online === null ? (
            <span className="inline-block size-1.5 rounded-full bg-muted-foreground" />
          ) : online ? (
            <span className="inline-block size-1.5 animate-pulse rounded-full bg-green-500" />
          ) : (
            <span className="inline-block size-1.5 rounded-full bg-destructive" />
          )}
          <span className={cn(
            'text-sm',
            online === null && 'text-muted-foreground',
            online === true && 'text-foreground',
            online === false && 'text-destructive',
          )}>
            {online === null ? 'Connecting…' : online ? 'Online' : 'Offline'}
          </span>
        </div>

        {health && (
          <>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Zap className="size-3.5 shrink-0" />
              <span className="truncate">{health.model.replace('claude-', '')}</span>
            </div>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Activity className="size-3.5 shrink-0" />
              <span>
                {health.tracked_symbols.length} symbol{health.tracked_symbols.length !== 1 ? 's' : ''}
              </span>
            </div>

            <div className="flex flex-wrap gap-1">
              {health.tracked_symbols.map(s => (
                <Badge key={s} variant="secondary" className="px-1.5 py-0 text-xs font-mono">
                  {s.replace('USDT', '')}
                </Badge>
              ))}
            </div>
          </>
        )}
      </div>

      {/* ── Last cycle ─────────────────────────────────────────────────── */}
      {lastCycle && (
        <>
          <Separator />
          <div className="flex flex-col gap-1 p-4">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <RefreshCw className="size-3" />
              Last cycle
            </div>
            <p className="font-mono text-xs text-foreground">{fmtCycleTime(lastCycle)}</p>
          </div>
        </>
      )}

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <div className="mt-auto p-4">
        <p className="text-xs text-muted-foreground">Auto-refresh every 30 s</p>
      </div>
    </aside>
  )
}

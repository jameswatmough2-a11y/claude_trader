'use client'

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import {
  DEFAULT_PREFS,
  getCurrencyMeta,
  loadDisplayPrefs,
  saveDisplayPrefs,
  type DisplayPrefs,
} from '@/lib/display-prefs'

// ── Context ───────────────────────────────────────────────────────────────────

interface DisplayPrefsCtx {
  prefs: DisplayPrefs
  setPrefs: (next: DisplayPrefs) => void
  /** Convert a USD amount to the display currency */
  cvtPrice: (usd: number) => number
  /** Currency symbol for the active currency */
  currencySymbol: string
  /** Format a UTC ISO timestamp in the active timezone */
  fmtTime: (iso: string) => string
}

const Ctx = createContext<DisplayPrefsCtx>({
  prefs: DEFAULT_PREFS,
  setPrefs: () => {},
  cvtPrice: v => v,
  currencySymbol: '$',
  fmtTime: iso => new Date(iso).toLocaleString(),
})

// ── Provider ──────────────────────────────────────────────────────────────────

export function DisplayPrefsProvider({ children }: { children: React.ReactNode }) {
  const [prefs, setPrefsState] = useState<DisplayPrefs>(DEFAULT_PREFS)

  // Hydrate from localStorage after mount (avoids SSR mismatch), then sync from backend
  useEffect(() => {
    const local = loadDisplayPrefs()
    setPrefsState(local)
    // Sync timezone/currency from backend config (authoritative source)
    fetch('/api/bot/config')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return
        const synced: DisplayPrefs = {
          ...local,
          ...(data.timezone ? { timezone: data.timezone } : {}),
          ...(data.display_currency ? { currency: data.display_currency } : {}),
        }
        setPrefsState(synced)
        saveDisplayPrefs(synced)
      })
      .catch(() => {})
  }, [])

  const setPrefs = useCallback((next: DisplayPrefs) => {
    setPrefsState(next)
    saveDisplayPrefs(next)
  }, [])

  const currency = getCurrencyMeta(prefs.currency)

  const cvtPrice = useCallback((usd: number) => usd * currency.rate, [currency.rate])

  const fmtTime = useCallback((iso: string) => {
    return new Date(iso).toLocaleString('en', {
      timeZone: prefs.timezone,
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  }, [prefs.timezone])

  return (
    <Ctx.Provider value={{ prefs, setPrefs, cvtPrice, currencySymbol: currency.symbol, fmtTime }}>
      {children}
    </Ctx.Provider>
  )
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useDisplayPrefs() {
  return useContext(Ctx)
}

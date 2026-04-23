'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Lock, RotateCcw, Save } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { CURRENCIES, TIMEZONES } from '@/lib/display-prefs'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

// ── Types ─────────────────────────────────────────────────────────────────────

const CHART_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const

const CLAUDE_MODELS = [
  'claude-sonnet-4-6',
  'claude-haiku-4-5-20251001',
  'claude-opus-4-7',
] as const

interface Config {
  interval_minutes: number
  min_confidence: number
  max_position_pct: number
  max_total_exposure_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  tracked_symbols: string
  paper_balance_usdt: number
  chart_interval: string
  model_name: string
  timezone: string
  display_currency: string
}

const DEFAULTS: Config = {
  interval_minutes: 60,
  min_confidence: 0.7,
  max_position_pct: 20.0,
  max_total_exposure_pct: 60.0,
  stop_loss_pct: 5.0,
  take_profit_pct: 0.0,
  tracked_symbols: 'BTCUSDT,ETHUSDT,SOLUSDT',
  paper_balance_usdt: 10000,
  chart_interval: '1m',
  model_name: 'claude-sonnet-4-6',
  timezone: 'UTC',
  display_currency: 'USD',
}

// Trading-critical keys — locked when bot is running
const TRADING_KEYS: (keyof Config)[] = [
  'tracked_symbols', 'interval_minutes', 'model_name',
  'min_confidence', 'max_position_pct', 'max_total_exposure_pct',
  'stop_loss_pct', 'take_profit_pct', 'paper_balance_usdt',
]

function parseSymbols(raw: string): string[] {
  return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {children}
      </span>
      <div className="h-px flex-1 bg-border" />
    </div>
  )
}

function SettingField({
  label, description, children,
}: {
  label: string; description: string; children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {children}
      <p className="text-xs text-muted-foreground">{description}</p>
    </div>
  )
}

function NumberInput({ value, onChange, min, max, step = 1, disabled }: {
  value: number; onChange: (v: number) => void
  min: number; max: number; step?: number; disabled?: boolean
}) {
  return (
    <input
      type="number" value={value} min={min} max={max} step={step} disabled={disabled}
      onChange={e => onChange(Number(e.target.value))}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1',
        'text-sm shadow-sm transition-colors',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
      )}
    />
  )
}

function TextInput({ value, onChange, placeholder, disabled }: {
  value: string; onChange: (v: string) => void; placeholder?: string; disabled?: boolean
}) {
  return (
    <input
      type="text" value={value} placeholder={placeholder} disabled={disabled}
      onChange={e => onChange(e.target.value)}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1',
        'font-mono text-sm shadow-sm transition-colors',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
      )}
    />
  )
}

type SaveStatus = 'idle' | 'saved' | 'error'

function SaveBar({
  isDirty, saving, saveStatus, onSave, onRevert,
  locked, lockedMessage,
}: {
  isDirty: boolean
  saving: boolean
  saveStatus: SaveStatus
  onSave: () => void
  onRevert?: () => void
  locked?: boolean
  lockedMessage?: string
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-card px-4 py-3">
      <p className={cn(
        'text-xs',
        saveStatus === 'saved' && 'text-green-600 dark:text-green-400',
        saveStatus === 'error' && 'text-destructive',
        saveStatus === 'idle' && isDirty && 'text-muted-foreground',
        saveStatus === 'idle' && !isDirty && 'text-muted-foreground/40',
      )}>
        {saveStatus === 'saved' ? 'Saved' :
         saveStatus === 'error' ? 'Save failed — check server logs' :
         locked ? (lockedMessage ?? 'Locked while bot is running') :
         isDirty ? 'Unsaved changes' : 'All changes saved'}
      </p>
      <div className="flex items-center gap-2">
        {onRevert && (
          <Button
            variant="ghost" size="sm"
            onClick={onRevert}
            disabled={!isDirty || saving || locked}
          >
            <RotateCcw data-icon="inline-start" />
            Revert
          </Button>
        )}
        <Button
          size="sm"
          onClick={onSave}
          disabled={!isDirty || saving || locked}
        >
          <Save data-icon="inline-start" />
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [config, setConfig] = useState<Config>(DEFAULTS)
  const [saved, setSaved] = useState<Config>(DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [botRunning, setBotRunning] = useState<boolean | null>(null)
  const statusIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const [tradingSaving, setTradingSaving] = useState(false)
  const [tradingSaveStatus, setTradingSaveStatus] = useState<SaveStatus>('idle')

  const [displaySaving, setDisplaySaving] = useState(false)
  const [displaySaveStatus, setDisplaySaveStatus] = useState<SaveStatus>('idle')

  const { prefs: displayPrefs, setPrefs: setDisplayPrefs } = useDisplayPrefs()

  // ── Data loading ────────────────────────────────────────────────────────────

  const toClean = (data: Partial<Config> & Record<string, unknown>): Config => ({
    interval_minutes: (data.interval_minutes as number) ?? DEFAULTS.interval_minutes,
    min_confidence: (data.min_confidence as number) ?? DEFAULTS.min_confidence,
    max_position_pct: (data.max_position_pct as number) ?? DEFAULTS.max_position_pct,
    max_total_exposure_pct: (data.max_total_exposure_pct as number) ?? DEFAULTS.max_total_exposure_pct,
    stop_loss_pct: (data.stop_loss_pct as number) ?? DEFAULTS.stop_loss_pct,
    take_profit_pct: (data.take_profit_pct as number) ?? DEFAULTS.take_profit_pct,
    tracked_symbols: (data.tracked_symbols as string) ?? DEFAULTS.tracked_symbols,
    paper_balance_usdt: (data.paper_balance_usdt as number) ?? DEFAULTS.paper_balance_usdt,
    chart_interval: (data.chart_interval as string) ?? DEFAULTS.chart_interval,
    model_name: (data.model_name as string) ?? DEFAULTS.model_name,
    timezone: (data.timezone as string) ?? DEFAULTS.timezone,
    display_currency: (data.display_currency as string) ?? DEFAULTS.display_currency,
  })

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/bot/status')
      if (res.ok) setBotRunning((await res.json()).running ?? false)
    } catch {}
  }, [])

  const load = useCallback(async () => {
    try {
      const [configRes] = await Promise.all([fetch('/api/bot/config'), fetchStatus()])
      if (!configRes.ok) throw new Error(configRes.statusText)
      const clean = toClean(await configRes.json())
      setConfig(clean)
      setSaved(clean)
      setError(null)
    } catch {
      setError('Cannot reach bot API — is the server running on localhost:8000?')
    } finally {
      setLoading(false)
    }
  }, [fetchStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load()
    statusIntervalRef.current = setInterval(fetchStatus, 5000)
    return () => { if (statusIntervalRef.current) clearInterval(statusIntervalRef.current) }
  }, [load, fetchStatus])

  // ── Save handlers ───────────────────────────────────────────────────────────

  async function saveTradingSettings() {
    setTradingSaving(true)
    setTradingSaveStatus('idle')
    try {
      const res = await fetch('/api/bot/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      if (!res.ok) throw new Error(await res.text())
      const clean = toClean(await res.json())
      setConfig(clean)
      setSaved(clean)
      setTradingSaveStatus('saved')
      setTimeout(() => setTradingSaveStatus('idle'), 2500)
    } catch {
      setTradingSaveStatus('error')
    } finally {
      setTradingSaving(false)
    }
  }

  async function saveDisplaySettings() {
    setDisplaySaving(true)
    setDisplaySaveStatus('idle')
    try {
      // Send saved trading values + updated display fields only
      const res = await fetch('/api/bot/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...saved,
          chart_interval: config.chart_interval,
          timezone: config.timezone,
          display_currency: config.display_currency,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const clean = toClean(await res.json())
      setConfig(prev => ({
        ...prev,
        chart_interval: clean.chart_interval,
        timezone: clean.timezone,
        display_currency: clean.display_currency,
      }))
      setSaved(prev => ({
        ...prev,
        chart_interval: clean.chart_interval,
        timezone: clean.timezone,
        display_currency: clean.display_currency,
      }))
      // Apply display prefs globally after successful save
      setDisplayPrefs({ ...displayPrefs, timezone: clean.timezone, currency: clean.display_currency })
      setDisplaySaveStatus('saved')
      setTimeout(() => setDisplaySaveStatus('idle'), 2500)
    } catch {
      setDisplaySaveStatus('error')
    } finally {
      setDisplaySaving(false)
    }
  }

  // ── Derived state ───────────────────────────────────────────────────────────

  const tradingLocked = botRunning === true
  const isTradingDirty = TRADING_KEYS.some(k => config[k] !== saved[k])
  const isDisplayDirty = (
    config.chart_interval !== saved.chart_interval ||
    config.timezone !== saved.timezone ||
    config.display_currency !== saved.display_currency
  )

  const setNum = (key: keyof Config) => (v: number) => setConfig(c => ({ ...c, [key]: v }))
  const setStr = (key: keyof Config) => (v: string) => setConfig(c => ({ ...c, [key]: v }))

  const symbolList = parseSymbols(config.tracked_symbols)
  const symbolsChanged = config.tracked_symbols !== saved.tracked_symbols

  const td = loading || tradingSaving || tradingLocked  // trading field disabled
  const dd = loading || displaySaving                   // display field disabled

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 p-6">

      <h1 className="text-base font-semibold">Settings</h1>

      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pb-4 pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* ── Trading Settings ─────────────────────────────────────────────── */}

      <SectionHeading>Trading Settings</SectionHeading>

      {tradingLocked && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardContent className="flex items-center gap-3 pb-4 pt-4">
            <Lock className="size-4 shrink-0 text-amber-500" />
            <p className="text-sm text-amber-600 dark:text-amber-400">
              Bot is running — stop it to edit trading settings.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Tracked symbols */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Tracked Symbols</CardTitle>
          <CardDescription>Crypto pairs the bot monitors and trades.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SettingField
            label="Symbols (comma-separated)"
            description="Binance USDT pairs, e.g. BTCUSDT,ETHUSDT,SOLUSDT. Saving reconnects the price stream automatically."
          >
            <TextInput
              value={config.tracked_symbols}
              onChange={setStr('tracked_symbols')}
              placeholder="BTCUSDT,ETHUSDT,SOLUSDT"
              disabled={td}
            />
          </SettingField>
          {symbolList.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {symbolList.map(s => (
                <Badge key={s} variant={symbolsChanged ? 'outline' : 'secondary'} className="font-mono text-xs">
                  {s}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Trading cycle */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Trading Cycle</CardTitle>
          <CardDescription>How often Claude re-analyses the market.</CardDescription>
        </CardHeader>
        <CardContent>
          <SettingField
            label="Reanalysis Interval (minutes)"
            description="Minutes between each AI decision cycle. Min 1, max 1440 (24 h)."
          >
            <NumberInput value={config.interval_minutes} onChange={setNum('interval_minutes')} min={1} max={1440} disabled={td} />
          </SettingField>
        </CardContent>
      </Card>

      {/* AI model */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">AI Model</CardTitle>
          <CardDescription>Claude model used for trading decisions.</CardDescription>
        </CardHeader>
        <CardContent>
          <SettingField
            label="Model"
            description="Applied to the next trading cycle. Faster models are cheaper; larger models reason better."
          >
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap gap-1.5">
                {CLAUDE_MODELS.map(m => (
                  <button
                    key={m}
                    onClick={() => setStr('model_name')(m)}
                    disabled={td}
                    className={cn(
                      'h-8 rounded-md px-3 text-xs font-mono font-medium transition-colors',
                      'border disabled:cursor-not-allowed disabled:opacity-50',
                      config.model_name === m
                        ? 'border-primary bg-secondary text-secondary-foreground'
                        : 'border-input bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                    )}
                  >
                    {m.replace('claude-', '')}
                  </button>
                ))}
              </div>
              <TextInput value={config.model_name} onChange={setStr('model_name')} placeholder="claude-sonnet-4-6" disabled={td} />
            </div>
          </SettingField>
        </CardContent>
      </Card>

      {/* AI thresholds */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">AI Decision Thresholds</CardTitle>
          <CardDescription>Controls when Claude's recommendations are acted on.</CardDescription>
        </CardHeader>
        <CardContent>
          <SettingField
            label="Min Confidence (0 – 1)"
            description="Decisions below this threshold are overridden to HOLD. Passed directly to Claude in the system prompt."
          >
            <NumberInput value={config.min_confidence} onChange={setNum('min_confidence')} min={0} max={1} step={0.01} disabled={td} />
          </SettingField>
        </CardContent>
      </Card>

      {/* Position sizing */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Position Sizing</CardTitle>
          <CardDescription>Maximum capital allocation per trade and in total.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SettingField
            label="Max Position Size (%)"
            description="Maximum portfolio % allocated to a single position."
          >
            <NumberInput value={config.max_position_pct} onChange={setNum('max_position_pct')} min={1} max={100} disabled={td} />
          </SettingField>
          <Separator />
          <SettingField
            label="Max Total Exposure (%)"
            description="Maximum combined portfolio % across all open positions simultaneously."
          >
            <NumberInput value={config.max_total_exposure_pct} onChange={setNum('max_total_exposure_pct')} min={1} max={100} disabled={td} />
          </SettingField>
        </CardContent>
      </Card>

      {/* Risk management */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Risk Management</CardTitle>
          <CardDescription>Automatic exits checked on every Binance price tick.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SettingField
            label="Stop Loss (%)"
            description="Exit when price drops this % below entry. Set to 0 to disable."
          >
            <NumberInput value={config.stop_loss_pct} onChange={setNum('stop_loss_pct')} min={0} max={50} step={0.1} disabled={td} />
          </SettingField>
          <Separator />
          <SettingField
            label="Take Profit (%)"
            description="Exit when price rises this % above entry. Set to 0 to disable."
          >
            <NumberInput value={config.take_profit_pct} onChange={setNum('take_profit_pct')} min={0} max={100} step={0.1} disabled={td} />
          </SettingField>
        </CardContent>
      </Card>

      {/* Paper trading */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Paper Trading</CardTitle>
          <CardDescription>Simulated balance used when PAPER_TRADING=true.</CardDescription>
        </CardHeader>
        <CardContent>
          <SettingField
            label="Starting Balance (USDT)"
            description="Virtual USDT balance for paper trades. Takes effect after Reset DB."
          >
            <NumberInput value={config.paper_balance_usdt} onChange={setNum('paper_balance_usdt')} min={100} max={10_000_000} step={100} disabled={td} />
          </SettingField>
        </CardContent>
      </Card>

      <SaveBar
        isDirty={isTradingDirty}
        saving={tradingSaving}
        saveStatus={tradingSaveStatus}
        onSave={saveTradingSettings}
        onRevert={() => setConfig(saved)}
        locked={tradingLocked}
      />

      {/* ── Display Settings ─────────────────────────────────────────────── */}

      <SectionHeading>Display Settings</SectionHeading>

      {/* Chart interval */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Default Chart Interval</CardTitle>
          <CardDescription>The timeframe selected when the dashboard loads.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-1.5">
            {CHART_INTERVALS.map(tf => (
              <button
                key={tf}
                onClick={() => setStr('chart_interval')(tf)}
                disabled={dd}
                className={cn(
                  'h-8 rounded-md px-3 text-sm font-mono font-medium transition-colors',
                  'border disabled:cursor-not-allowed disabled:opacity-50',
                  config.chart_interval === tf
                    ? 'border-primary bg-secondary text-secondary-foreground'
                    : 'border-input bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                )}
              >
                {tf}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Currency + Timezone */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">Currency &amp; Timezone</CardTitle>
          <CardDescription>
            Affects all price and time displays across the app. Prices are multiplied by an approximate rate — all trading uses USD internally.
            Saved to the database and applied globally on save.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SettingField label="Currency" description="">
            <div className="flex flex-wrap gap-1.5">
              {CURRENCIES.map(c => (
                <button
                  key={c.code}
                  disabled={dd}
                  onClick={() => setStr('display_currency')(c.code)}
                  className={cn(
                    'h-8 rounded-md px-3 text-sm font-mono font-medium transition-colors border',
                    'disabled:cursor-not-allowed disabled:opacity-50',
                    config.display_currency === c.code
                      ? 'border-primary bg-secondary text-secondary-foreground'
                      : 'border-input bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                  )}
                >
                  {c.symbol} {c.code}
                </button>
              ))}
            </div>
          </SettingField>
          <Separator />
          <SettingField label="Timezone" description="">
            <div className="flex flex-wrap gap-1.5">
              {TIMEZONES.map(tz => (
                <button
                  key={tz.iana}
                  disabled={dd}
                  onClick={() => setStr('timezone')(tz.iana)}
                  className={cn(
                    'h-8 rounded-md px-3 text-sm font-mono font-medium transition-colors border',
                    'disabled:cursor-not-allowed disabled:opacity-50',
                    config.timezone === tz.iana
                      ? 'border-primary bg-secondary text-secondary-foreground'
                      : 'border-input bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                  )}
                >
                  {tz.label}
                </button>
              ))}
            </div>
          </SettingField>
        </CardContent>
      </Card>

      <SaveBar
        isDirty={isDisplayDirty}
        saving={displaySaving}
        saveStatus={displaySaveStatus}
        onSave={saveDisplaySettings}
        onRevert={() => setConfig(prev => ({
          ...prev,
          chart_interval: saved.chart_interval,
          timezone: saved.timezone,
          display_currency: saved.display_currency,
        }))}
      />

    </div>
  )
}

'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Lock, RotateCcw, Save } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { CURRENCIES, TIMEZONES } from '@/lib/display-prefs'
import { useDisplayPrefs } from '@/app/providers/display-prefs-provider'

// ── Types ─────────────────────────────────────────────────────────────────────

const CHART_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
const OHLCV_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const

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
  ohlcv_interval: string
  taker_fee_rate: number
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
  ohlcv_interval: '1h',
  taker_fee_rate: 0.001,
}

const DISPLAY_KEYS: (keyof Config)[] = ['chart_interval', 'timezone', 'display_currency']

function parseSymbols(raw: string): string[] {
  return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionHeading({ children, locked }: { children: React.ReactNode; locked?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        {children}
      </span>
      {locked && <Lock className="size-3 text-amber-500/80" />}
      <div className="h-px flex-1 bg-border" />
    </div>
  )
}

function SettingRow({
  label, description, children,
}: {
  label: string; description: string; children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-2">
      <div>
        <p className="text-sm font-medium">{label}</p>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {children}
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

function SaveBar({ isDirty, saving, saveStatus, onSave, onRevert }: {
  isDirty: boolean
  saving: boolean
  saveStatus: SaveStatus
  onSave: () => void
  onRevert: () => void
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
         isDirty ? 'Unsaved changes' : 'All changes saved'}
      </p>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onRevert} disabled={!isDirty || saving}>
          <RotateCcw data-icon="inline-start" />
          Revert
        </Button>
        <Button size="sm" onClick={onSave} disabled={!isDirty || saving}>
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

  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')

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
    ohlcv_interval: (data.ohlcv_interval as string) ?? DEFAULTS.ohlcv_interval,
    taker_fee_rate: (data.taker_fee_rate as number) ?? DEFAULTS.taker_fee_rate,
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

  // ── Save / revert ───────────────────────────────────────────────────────────

  async function saveSettings() {
    setSaving(true)
    setSaveStatus('idle')
    try {
      const body = tradingLocked
        ? { ...saved, chart_interval: config.chart_interval, timezone: config.timezone, display_currency: config.display_currency }
        : config
      const res = await fetch('/api/bot/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await res.text())
      const clean = toClean(await res.json())
      setConfig(clean)
      setSaved(clean)
      setDisplayPrefs({ ...displayPrefs, timezone: clean.timezone, currency: clean.display_currency })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2500)
    } catch {
      setSaveStatus('error')
    } finally {
      setSaving(false)
    }
  }

  function revert() {
    if (tradingLocked) {
      setConfig(prev => ({
        ...prev,
        chart_interval: saved.chart_interval,
        timezone: saved.timezone,
        display_currency: saved.display_currency,
      }))
    } else {
      setConfig(saved)
    }
  }

  // ── Derived state ───────────────────────────────────────────────────────────

  const tradingLocked = botRunning === true
  const isDirty = tradingLocked
    ? DISPLAY_KEYS.some(k => config[k] !== saved[k])
    : (Object.keys(DEFAULTS) as (keyof Config)[]).some(k => config[k] !== saved[k])

  const setNum = (key: keyof Config) => (v: number) => setConfig(c => ({ ...c, [key]: v }))
  const setStr = (key: keyof Config) => (v: string) => setConfig(c => ({ ...c, [key]: v }))

  const symbolList = parseSymbols(config.tracked_symbols)
  const symbolsChanged = config.tracked_symbols !== saved.tracked_symbols

  const td = loading || saving || tradingLocked
  const dd = loading || saving

  const btnCls = (active: boolean) => cn(
    'h-8 rounded-md px-3 text-sm font-mono font-medium transition-colors border',
    'disabled:cursor-not-allowed disabled:opacity-50',
    active
      ? 'border-primary bg-secondary text-secondary-foreground'
      : 'border-input bg-background text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
  )

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="flex min-h-screen w-full flex-col gap-6 p-4 md:p-6">

      <h1 className="text-base font-semibold">Settings</h1>

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {tradingLocked && (
        <div className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3">
          <Lock className="size-4 shrink-0 text-amber-500" />
          <p className="text-sm text-amber-600 dark:text-amber-400">
            Bot is running — AI model and trading settings are locked.
          </p>
        </div>
      )}

      {/* ── AI Model + Trading columns ──────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 md:items-start">

        {/* AI Model */}
        <div className="flex flex-col gap-5">
          <SectionHeading locked={tradingLocked}>AI Model</SectionHeading>

          <div className="flex flex-col divide-y">

            <div className="py-5">
              <SettingRow
                label="Model"
                description="Applied to the next trading cycle. Faster models are cheaper; larger models reason better."
              >
                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap gap-1.5">
                    {CLAUDE_MODELS.map(m => (
                      <button key={m} onClick={() => setStr('model_name')(m)} disabled={td} className={btnCls(config.model_name === m)}>
                        {m.replace('claude-', '')}
                      </button>
                    ))}
                  </div>
                  <TextInput value={config.model_name} onChange={setStr('model_name')} placeholder="claude-sonnet-4-6" disabled={td} />
                </div>
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Reanalysis Interval"
                description="Minutes between each AI decision cycle. Min 1, max 1440 (24 h)."
              >
                <NumberInput value={config.interval_minutes} onChange={setNum('interval_minutes')} min={1} max={1440} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Min Confidence"
                description="Decisions below this threshold are overridden to HOLD. Passed to Claude in the system prompt."
              >
                <NumberInput value={config.min_confidence} onChange={setNum('min_confidence')} min={0} max={1} step={0.01} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="OHLCV Interval"
                description="Candle timeframe for AI analysis. Candles are fetched at this interval each cycle."
              >
                <div className="flex flex-wrap gap-1.5">
                  {OHLCV_INTERVALS.map(tf => (
                    <button key={tf} onClick={() => setStr('ohlcv_interval')(tf)} disabled={td} className={btnCls(config.ohlcv_interval === tf)}>
                      {tf}
                    </button>
                  ))}
                </div>
              </SettingRow>
            </div>

          </div>
        </div>

        {/* Trading */}
        <div className="flex flex-col gap-5">
          <SectionHeading locked={tradingLocked}>Trading</SectionHeading>

          <div className="flex flex-col divide-y">

            <div className="py-5">
              <SettingRow
                label="Tracked Symbols"
                description="Binance USDT pairs e.g. BTCUSDT,ETHUSDT,SOLUSDT. Saving reconnects the price stream."
              >
                <div className="flex flex-col gap-2.5">
                  <TextInput
                    value={config.tracked_symbols}
                    onChange={setStr('tracked_symbols')}
                    placeholder="BTCUSDT,ETHUSDT,SOLUSDT"
                    disabled={td}
                  />
                  {symbolList.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {symbolList.map(s => (
                        <Badge key={s} variant={symbolsChanged ? 'outline' : 'secondary'} className="font-mono text-xs">
                          {s}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Max Position Size (%)"
                description="Maximum portfolio % allocated to a single position."
              >
                <NumberInput value={config.max_position_pct} onChange={setNum('max_position_pct')} min={1} max={100} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Max Total Exposure (%)"
                description="Maximum combined portfolio % across all open positions simultaneously."
              >
                <NumberInput value={config.max_total_exposure_pct} onChange={setNum('max_total_exposure_pct')} min={1} max={100} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Stop Loss (%)"
                description="Exit when price drops this % below entry. Set to 0 to disable."
              >
                <NumberInput value={config.stop_loss_pct} onChange={setNum('stop_loss_pct')} min={0} max={50} step={0.1} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Take Profit (%)"
                description="Exit when price rises this % above entry. Set to 0 to disable."
              >
                <NumberInput value={config.take_profit_pct} onChange={setNum('take_profit_pct')} min={0} max={100} step={0.1} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Paper Balance (USDT)"
                description="Virtual USDT balance for paper trades. Takes effect after Reset DB."
              >
                <NumberInput value={config.paper_balance_usdt} onChange={setNum('paper_balance_usdt')} min={100} max={10_000_000} step={100} disabled={td} />
              </SettingRow>
            </div>

            <div className="py-5">
              <SettingRow
                label="Taker Fee Rate"
                description="Exchange taker fee as a decimal (0.001 = 0.1%). Applied to paper fills and estimated for live."
              >
                <NumberInput value={config.taker_fee_rate} onChange={setNum('taker_fee_rate')} min={0} max={0.05} step={0.0001} disabled={td} />
              </SettingRow>
            </div>

          </div>
        </div>

      </div>

      {/* ── Display ────────────────────────────────────────────────────────────── */}
      <div className="flex flex-col gap-5">
        <SectionHeading>Display</SectionHeading>

        <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">

          <SettingRow
            label="Chart Interval"
            description="Default timeframe when the dashboard loads."
          >
            <div className="flex flex-wrap gap-1.5">
              {CHART_INTERVALS.map(tf => (
                <button key={tf} onClick={() => setStr('chart_interval')(tf)} disabled={dd} className={btnCls(config.chart_interval === tf)}>
                  {tf}
                </button>
              ))}
            </div>
          </SettingRow>

          <SettingRow
            label="Currency"
            description="All price displays use this currency. Trading always uses USD internally."
          >
            <div className="flex flex-wrap gap-1.5">
              {CURRENCIES.map(c => (
                <button key={c.code} disabled={dd} onClick={() => setStr('display_currency')(c.code)} className={btnCls(config.display_currency === c.code)}>
                  {c.symbol} {c.code}
                </button>
              ))}
            </div>
          </SettingRow>

          <SettingRow
            label="Timezone"
            description="Affects all time displays. Applied globally on save."
          >
            <div className="flex flex-wrap gap-1.5">
              {TIMEZONES.map(tz => (
                <button key={tz.iana} disabled={dd} onClick={() => setStr('timezone')(tz.iana)} className={btnCls(config.timezone === tz.iana)}>
                  {tz.label}
                </button>
              ))}
            </div>
          </SettingRow>

        </div>
      </div>

      <SaveBar
        isDirty={isDirty}
        saving={saving}
        saveStatus={saveStatus}
        onSave={saveSettings}
        onRevert={revert}
      />

    </div>
  )
}

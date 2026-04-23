'use client'

import { useCallback, useEffect, useState } from 'react'
import { Save, RotateCcw } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Config {
  interval_minutes: number
  min_confidence: number
  max_position_pct: number
  max_total_exposure_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  tracked_symbols: string
  paper_balance_usdt: number
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
}

function parseSymbols(raw: string): string[] {
  return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SettingField({
  label,
  description,
  children,
}: {
  label: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {children}
      <p className="text-xs text-muted-foreground">{description}</p>
    </div>
  )
}

function NumberInput({
  value,
  onChange,
  min,
  max,
  step = 1,
  disabled,
}: {
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step?: number
  disabled?: boolean
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
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

function TextInput({
  value,
  onChange,
  placeholder,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  disabled?: boolean
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      disabled={disabled}
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

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [config, setConfig] = useState<Config>(DEFAULTS)
  const [saved, setSaved] = useState<Config>(DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  const toClean = (data: Config): Config => ({
    interval_minutes: data.interval_minutes,
    min_confidence: data.min_confidence,
    max_position_pct: data.max_position_pct,
    max_total_exposure_pct: data.max_total_exposure_pct,
    stop_loss_pct: data.stop_loss_pct,
    take_profit_pct: data.take_profit_pct,
    tracked_symbols: data.tracked_symbols ?? DEFAULTS.tracked_symbols,
    paper_balance_usdt: data.paper_balance_usdt ?? DEFAULTS.paper_balance_usdt,
  })

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/bot/config')
      if (!res.ok) throw new Error(res.statusText)
      const clean = toClean(await res.json())
      setConfig(clean)
      setSaved(clean)
      setError(null)
    } catch {
      setError('Cannot reach bot API — is the server running on localhost:8000?')
    } finally {
      setLoading(false)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load() }, [load])

  async function save() {
    setSaving(true)
    setSaveStatus('idle')
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
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2500)
    } catch {
      setSaveStatus('error')
    } finally {
      setSaving(false)
    }
  }

  function revert() {
    setConfig(saved)
    setSaveStatus('idle')
  }

  const isDirty = JSON.stringify(config) !== JSON.stringify(saved)
  const setNum = (key: keyof Config) => (v: number) => setConfig(c => ({ ...c, [key]: v }))
  const setStr = (key: keyof Config) => (v: string) => setConfig(c => ({ ...c, [key]: v }))

  const symbolList = parseSymbols(config.tracked_symbols)
  const symbolsChanged = config.tracked_symbols !== saved.tracked_symbols

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
              disabled={loading || saving}
            />
          </SettingField>

          {symbolList.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {symbolList.map(s => (
                <Badge
                  key={s}
                  variant={symbolsChanged ? 'outline' : 'secondary'}
                  className="font-mono text-xs"
                >
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
            description="Minutes between each AI decision cycle. Min 1, max 1440 (24 h). If the bot is running, it reschedules immediately."
          >
            <NumberInput
              value={config.interval_minutes}
              onChange={setNum('interval_minutes')}
              min={1}
              max={1440}
              disabled={loading || saving}
            />
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
            description="Decisions with confidence below this threshold are overridden to HOLD. This value is also passed directly to Claude in the system prompt."
          >
            <NumberInput
              value={config.min_confidence}
              onChange={setNum('min_confidence')}
              min={0}
              max={1}
              step={0.01}
              disabled={loading || saving}
            />
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
            description="Maximum portfolio % allocated to a single position. Also caps size_pct in Claude's response."
          >
            <NumberInput
              value={config.max_position_pct}
              onChange={setNum('max_position_pct')}
              min={1}
              max={100}
              disabled={loading || saving}
            />
          </SettingField>

          <Separator />

          <SettingField
            label="Max Total Exposure (%)"
            description="Maximum combined portfolio % across all open positions simultaneously."
          >
            <NumberInput
              value={config.max_total_exposure_pct}
              onChange={setNum('max_total_exposure_pct')}
              min={1}
              max={100}
              disabled={loading || saving}
            />
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
            <NumberInput
              value={config.stop_loss_pct}
              onChange={setNum('stop_loss_pct')}
              min={0}
              max={50}
              step={0.1}
              disabled={loading || saving}
            />
          </SettingField>

          <Separator />

          <SettingField
            label="Take Profit (%)"
            description="Exit when price rises this % above entry. Set to 0 to disable."
          >
            <NumberInput
              value={config.take_profit_pct}
              onChange={setNum('take_profit_pct')}
              min={0}
              max={100}
              step={0.1}
              disabled={loading || saving}
            />
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
            <NumberInput
              value={config.paper_balance_usdt}
              onChange={setNum('paper_balance_usdt')}
              min={100}
              max={10_000_000}
              step={100}
              disabled={loading || saving}
            />
          </SettingField>
        </CardContent>
      </Card>

      {/* Save bar */}
      <div className="flex items-center justify-between rounded-lg border bg-card px-4 py-3">
        <p className={cn(
          'text-xs',
          saveStatus === 'saved' && 'text-green-600',
          saveStatus === 'error' && 'text-destructive',
          saveStatus === 'idle' && isDirty && 'text-muted-foreground',
          saveStatus === 'idle' && !isDirty && 'text-muted-foreground/50',
        )}>
          {saveStatus === 'saved' ? 'Settings saved' :
           saveStatus === 'error' ? 'Save failed — check server logs' :
           isDirty ? 'Unsaved changes' : 'All changes saved'}
        </p>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={revert} disabled={!isDirty || saving}>
            <RotateCcw data-icon="inline-start" />
            Revert
          </Button>
          <Button size="sm" onClick={save} disabled={!isDirty || saving || loading}>
            <Save data-icon="inline-start" />
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>

    </div>
  )
}

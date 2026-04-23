export interface DisplayPrefs {
  currency: string
  timezone: string
}

export const DEFAULT_PREFS: DisplayPrefs = { currency: 'USD', timezone: 'UTC' }

export const CURRENCIES = [
  { code: 'USD', symbol: '$',  rate: 1 },
  { code: 'EUR', symbol: '€',  rate: 0.93 },
  { code: 'GBP', symbol: '£',  rate: 0.79 },
  { code: 'JPY', symbol: '¥',  rate: 154 },
  { code: 'AUD', symbol: 'A$', rate: 1.56 },
  { code: 'CAD', symbol: 'C$', rate: 1.38 },
] as const

export const TIMEZONES = [
  { label: 'UTC',         iana: 'UTC' },
  { label: 'New York',    iana: 'America/New_York' },
  { label: 'Chicago',     iana: 'America/Chicago' },
  { label: 'Los Angeles', iana: 'America/Los_Angeles' },
  { label: 'London',      iana: 'Europe/London' },
  { label: 'Paris',       iana: 'Europe/Paris' },
  { label: 'Dubai',       iana: 'Asia/Dubai' },
  { label: 'Singapore',   iana: 'Asia/Singapore' },
  { label: 'Tokyo',       iana: 'Asia/Tokyo' },
  { label: 'Sydney',      iana: 'Australia/Sydney' },
] as const

export function getCurrencyMeta(code: string) {
  return CURRENCIES.find(c => c.code === code) ?? CURRENCIES[0]
}

export function loadDisplayPrefs(): DisplayPrefs {
  if (typeof window === 'undefined') return DEFAULT_PREFS
  try {
    const raw = localStorage.getItem('display-prefs')
    if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) }
  } catch {}
  return DEFAULT_PREFS
}

export function saveDisplayPrefs(prefs: DisplayPrefs): void {
  if (typeof window === 'undefined') return
  localStorage.setItem('display-prefs', JSON.stringify(prefs))
}

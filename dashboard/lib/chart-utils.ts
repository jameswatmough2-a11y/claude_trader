export function fmtPrice(n: number, decimals = 2): string {
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function fmtChange(pct: number | null): string | null {
  if (pct === null) return null
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
}

export function changeIsPositive(pct: number | null): boolean {
  return (pct ?? 0) >= 0
}

export function autoDecimals(price: number): number {
  if (price >= 1000) return 2
  if (price >= 1) return 4
  return 6
}

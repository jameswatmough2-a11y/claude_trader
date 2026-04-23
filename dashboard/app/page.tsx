import { CandlestickChart } from '@/app/components/candlestick-chart'
import { Dashboard } from '@/app/components/dashboard'

export default function Page() {
  return <Dashboard priceChart={<CandlestickChart />} />
}

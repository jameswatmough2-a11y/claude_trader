import { PriceChart } from '@/app/components/price-chart'
import { Dashboard } from '@/app/components/dashboard'

export default function Page() {
  return <Dashboard priceChart={<PriceChart />} />
}

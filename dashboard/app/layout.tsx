import { Geist, Geist_Mono } from 'next/font/google'

import './globals.css'
import { ThemeProvider } from '@/components/theme-provider'
import { AppSidebar } from '@/app/components/app-sidebar'
import { DisplayPrefsProvider } from '@/app/providers/display-prefs-provider'
import { cn } from '@/lib/utils'

const geist = Geist({ subsets: ['latin'], variable: '--font-sans' })
const fontMono = Geist_Mono({ subsets: ['latin'], variable: '--font-mono' })

export const metadata = {
  title: 'Claude Trader',
  description: 'Crypto trading bot dashboard',
}

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn('antialiased', fontMono.variable, 'font-sans', geist.variable)}
    >
      <body>
        <ThemeProvider>
          <DisplayPrefsProvider>
            <div className="flex min-h-screen">
              {/* Sidebar — hidden on small screens, visible md+ */}
              <div className="hidden md:flex">
                <AppSidebar />
              </div>

              {/* Main content */}
              <div className="flex min-h-screen flex-1 flex-col overflow-x-hidden">
                {children}
              </div>
            </div>
          </DisplayPrefsProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}

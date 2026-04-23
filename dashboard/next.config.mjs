/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/bot/:path*',
        destination: 'http://localhost:8000/:path*',
      },
      {
        source: '/api/chart/:path*',
        destination: 'http://localhost:8000/chart/:path*',
      },
      {
        source: '/api/logs/:path*',
        destination: 'http://localhost:8000/logs/:path*',
      },
      {
        source: '/api/logs',
        destination: 'http://localhost:8000/logs',
      },
      {
        source: '/api/sessions/:path*',
        destination: 'http://localhost:8000/sessions/:path*',
      },
      {
        source: '/api/sessions',
        destination: 'http://localhost:8000/sessions',
      },
      {
        source: '/api/trades/:path*',
        destination: 'http://localhost:8000/trades/:path*',
      },
      {
        source: '/api/trades',
        destination: 'http://localhost:8000/trades',
      },
    ]
  },
}

export default nextConfig

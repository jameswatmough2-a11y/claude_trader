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
    ]
  },
}

export default nextConfig

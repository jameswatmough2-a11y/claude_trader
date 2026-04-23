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
    ]
  },
}

export default nextConfig

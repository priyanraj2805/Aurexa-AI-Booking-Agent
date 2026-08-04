import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",

  devIndicators: {
    appIsrStatus: false,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        // Route all API requests to the Docker internal backend-super
        destination: process.env.INTERNAL_API_URL
          ? `${process.env.INTERNAL_API_URL}/api/v1/:path*`
          : 'http://backend-super:8006/api/v1/:path*',
      },
      {
        source: '/health',
        destination: process.env.INTERNAL_API_URL
          ? `${process.env.INTERNAL_API_URL}/health`
          : 'http://backend-super:8006/health',
      },
    ];
  },
};

export default nextConfig;

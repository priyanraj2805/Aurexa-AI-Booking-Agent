import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for Docker deployment — generates a self-contained server
  // in .next/standalone/ that doesn't need the full node_modules at runtime.
  output: "standalone",

  devIndicators: {
    appIsrStatus: false,
  },
  webpack: (config) => {
    config.externals = [...(config.externals || []), { canvas: "canvas" }];
    return config;
  },
  typescript: {
    // Disables type-checking during dev to save RAM.
    // Run `npx tsc --noEmit` manually or in CI.
    ignoreBuildErrors: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        // Route all API requests to the Docker internal backend
        destination: process.env.INTERNAL_API_URL 
          ? `${process.env.INTERNAL_API_URL}/api/v1/:path*` 
          : 'http://backend:8005/api/v1/:path*',
      },
      {
        source: '/static/widget/:path*',
        // Widget bundle is served by the backend, not the admin panel
        destination: process.env.INTERNAL_API_URL
          ? `${process.env.INTERNAL_API_URL}/static/widget/:path*`
          : 'http://backend:8005/static/widget/:path*',
      },
      {
        source: '/docs',
        destination: 'http://backend:8005/docs',
      },
      {
        source: '/openapi.json',
        destination: 'http://backend:8005/openapi.json',
      }
    ];
  },
};

export default nextConfig;

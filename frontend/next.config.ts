import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/ida/incidents",
        destination: "http://127.0.0.1:8000/api/v1/incidents",
      },
      {
        source: "/api/ida/endpoint-candidates",
        destination: "http://127.0.0.1:8000/api/v1/endpoint-candidates",
      },
      {
        source: "/api/ida/routes",
        destination: "http://127.0.0.1:8000/api/v1/routes",
      },
    ];
  },
};

export default nextConfig;
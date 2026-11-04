import type { NextConfig } from "next";

// Static export: `bun run build` writes plain HTML/JS/CSS to out/ (host on S3/CloudFront, nginx, or anywhere).
// No Node server in production. `bun run dev` runs the dev server with hot reload.
const nextConfig: NextConfig = {
  output: "export",
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

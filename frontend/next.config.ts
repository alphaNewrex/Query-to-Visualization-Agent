import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone: server.js plus only the node_modules files the app really loads.
  // frontend/Dockerfile copies that folder, .next/static and public/ into the runtime image.
  output: "standalone",
};

export default nextConfig;

import path from "node:path";
import createNextIntlPlugin from "next-intl/plugin";
import withBundleAnalyzerFactory from "@next/bundle-analyzer";

const withBundleAnalyzer = withBundleAnalyzerFactory({
  enabled: process.env.ANALYZE === "true",
  openAnalyzer: false,
});

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Keep development HMR artifacts isolated from `next build` output.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  eslint: { ignoreDuringBuilds: true },
  webpack: (config) => {
    // 消除 three/webgpu 重复构建（three-render-objects 静态引入但运行时用 WebGL）
    config.resolve.alias = {
      ...config.resolve.alias,
      "three/webgpu$": path.join(process.cwd(), "lib/three-webgpu-stub.mjs"),
    };
    return config;
  },
  async redirects() {
    // v0.3 客户端形态：旧路由 → 新 IA
    return [
      { source: "/overview", destination: "/chat", permanent: false },
      { source: "/assistants", destination: "/chat", permanent: false },
      { source: "/assistants/:id", destination: "/chat", permanent: false },
      { source: "/sources", destination: "/knowledge", permanent: false },
      { source: "/sources/:id", destination: "/knowledge/:id", permanent: false },
    ];
  },
};

export default withBundleAnalyzer(withNextIntl(nextConfig));

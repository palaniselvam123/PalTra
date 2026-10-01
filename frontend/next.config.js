const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: path.join(__dirname),
  ...(process.env.NEXT_OUTPUT === "export"
    ? { output: "export", trailingSlash: true, images: { unoptimized: true } }
    : {}),
};

module.exports = nextConfig;

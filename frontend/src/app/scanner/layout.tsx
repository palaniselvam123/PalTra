import type { Metadata } from "next";

// The browser tab names the page ("Scanner · PalTra", from the root layout's title template).
export const metadata: Metadata = { title: "Scanner" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}

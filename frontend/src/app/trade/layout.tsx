import type { Metadata } from "next";

// The browser tab names the page ("Trade · PalTra", from the root layout's title template).
export const metadata: Metadata = { title: "Trade" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}

import type { Metadata } from "next";

// The browser tab names the page ("US · PalTra", from the root layout's title template).
export const metadata: Metadata = { title: "US" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}

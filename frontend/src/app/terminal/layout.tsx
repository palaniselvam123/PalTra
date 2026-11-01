import type { Metadata } from "next";

// The browser tab names the page ("SMA Terminal · PalTra", from the root layout's title template).
export const metadata: Metadata = { title: "SMA Terminal" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}

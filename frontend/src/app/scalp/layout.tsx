import type { Metadata } from "next";

// The browser tab names the page ("Scalp · PalTra", from the root layout's title template).
export const metadata: Metadata = { title: "Scalp" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}

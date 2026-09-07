import type { Metadata } from "next";
import "./globals.css";
import { ChatWidget } from "@/components/Chat/ChatWidget";
import { NotificationProvider } from "@/components/Notifications/NotificationProvider";

export const metadata: Metadata = {
  title: "ORB Intraday Bot",
  description: "Intraday algorithmic trading dashboard — Paper Trading by default.",
};

/** Applies the stored theme before first paint.
 *
 *  Without this the page renders dark, React hydrates, and only then switches
 *  to light — a visible flash on every load for anyone using light mode. It is
 *  inline and synchronous on purpose: a deferred script would run too late to
 *  prevent that. Wrapped in try/catch because blocked localStorage must fall
 *  back to the default rather than break rendering. */
const themeScript = `
(function () {
  try {
    var t = localStorage.getItem('orb.theme');
    if (t !== 'light' && t !== 'dark') {
      t = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="bg-base text-slate-200 min-h-screen">
        <NotificationProvider>
          {children}
          <ChatWidget />
        </NotificationProvider>
      </body>
    </html>
  );
}

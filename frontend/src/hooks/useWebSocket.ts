"use client";

import { useEffect, useRef, useState } from "react";
import { WS_URL } from "@/lib/api";

export type WsMessage = { channel: string; data: any };

export function useWebSocket(onMessage: (msg: WsMessage) => void) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let openTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let delay = 2000;

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(WS_URL);
      openTimer = setTimeout(() => socket?.close(), 8000);

      socket.onopen = () => {
        if (openTimer) clearTimeout(openTimer);
        delay = 2000;
        if (!cancelled) setConnected(true);
      };
      socket.onclose = () => {
        if (openTimer) clearTimeout(openTimer);
        // A socket torn down by cleanup (StrictMode's double-mount, or a
        // re-render) must not report "disconnected" — its close event can
        // land after the replacement socket has already opened.
        if (cancelled) return;
        setConnected(false);
        retryTimer = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 15000);
      };
      socket.onerror = () => socket?.close();
      socket.onmessage = (event) => {
        try {
          handlerRef.current(JSON.parse(event.data));
        } catch {
          // ignore malformed frames
        }
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  return { connected };
}

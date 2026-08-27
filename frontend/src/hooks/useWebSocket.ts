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
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        if (!cancelled) setConnected(true);
      };
      socket.onclose = () => {
        // A socket torn down by cleanup (StrictMode's double-mount, or a
        // re-render) must not report "disconnected" — its close event can
        // land after the replacement socket has already opened.
        if (cancelled) return;
        setConnected(false);
        retryTimer = setTimeout(connect, 2000);
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

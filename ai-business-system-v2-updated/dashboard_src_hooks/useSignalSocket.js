/**
 * dashboard/src/hooks/useSignalSocket.js
 *
 * Live WebSocket connection to ws_broadcaster.py (port 8765).
 * Auto-reconnects every 3 seconds on drop.
 *
 * Returns:
 *   signals    — array of active trading signal objects
 *   connected  — boolean, true when WS is open
 */

import { useState, useEffect, useRef, useCallback } from "react";

const WS_URL = process.env.REACT_APP_WS_URL || "ws://localhost:8765";

export function useSignalSocket() {
  const [signals,   setSignals]   = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef    = useRef(null);
  const timerRef = useRef(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    wsRef.current = new WebSocket(WS_URL);

    wsRef.current.onopen = () => {
      setConnected(true);
      clearTimeout(timerRef.current);
    };

    wsRef.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "INIT") {
          setSignals(data.active_signals || []);

        } else if (data.type === "NEW_SIGNAL") {
          setSignals((prev) => [data.signal, ...prev]);

        } else if (data.type === "SIGNAL_INVALIDATED") {
          setSignals((prev) =>
            prev.map((s) =>
              s.ticker === data.ticker ? { ...s, status: "INVALIDATED" } : s
            )
          );
        }
      } catch (err) {
        console.error("WS message parse error:", err);
      }
    };

    wsRef.current.onclose = () => {
      setConnected(false);
      timerRef.current = setTimeout(connect, 3000);
    };

    wsRef.current.onerror = () => {
      wsRef.current?.close();
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { signals, connected };
}

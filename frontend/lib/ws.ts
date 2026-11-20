/**
 * Auction WebSocket client.
 *
 * Version: 2.0.0 (2026-11-20)
 *
 * Changed from 1.x, which was a bare socket with a fixed 3-second retry:
 *  - Exponential backoff with jitter. A fixed 3s retry means that when the
 *    backend restarts with 200 spectators connected, all 200 reconnect in the
 *    same instant, every 3 seconds, forever — a self-inflicted thundering herd
 *    during the window when the server is weakest.
 *  - A liveness watchdog. The server pings every 25s; 1.x never replied and
 *    never checked whether pings were still arriving. `onclose` only fires once
 *    TCP notices the connection is gone, which on a mobile network can take
 *    minutes or never — so a captain's screen would sit showing a stale bid and
 *    a frozen clock while the auction moved on without them. If nothing arrives
 *    for 45s we force-close and reconnect.
 *  - Replies to pings, and asks for a resync after any reconnect.
 *  - Reports connection state so the UI can show it.
 */
export type ConnectionState = "connecting" | "live" | "reconnecting" | "offline";

export interface AuctionSocketHandlers {
  onMessage: (data: any) => void;
  onStateChange?: (state: ConnectionState) => void;
}

const SILENCE_TIMEOUT_MS = 45_000;
const BASE_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
const MAX_ATTEMPTS_BEFORE_OFFLINE = 12;

export class AuctionSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: AuctionSocketHandlers;
  private closedByUs = false;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private hasConnectedBefore = false;

  constructor(eventId: number, token: string, handlers: AuctionSocketHandlers) {
    const base = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
    this.url = `${base}/api/auction/ws/${eventId}?token=${encodeURIComponent(token)}`;
    this.handlers = handlers;
  }

  private setState(state: ConnectionState) {
    this.handlers.onStateChange?.(state);
  }

  /** Exponential backoff with full jitter, so clients do not reconnect in lockstep. */
  private nextDelay(): number {
    const exponential = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** this.attempt);
    return Math.random() * exponential;
  }

  connect() {
    this.closedByUs = false;
    this.setState(this.hasConnectedBefore ? "reconnecting" : "connecting");

    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.attempt = 0;
      this.lastMessageAt = Date.now();
      this.setState("live");
      this.startWatchdog();

      // After a reconnect, ask for a fresh snapshot: we cannot know what was
      // missed while the socket was down.
      if (this.hasConnectedBefore) this.send({ type: "resync" });
      this.hasConnectedBefore = true;
    };

    this.ws.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      let data: any;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      // Keep the connection accounted for at both ends.
      if (data?.type === "ping") {
        this.send({ type: "pong" });
        return;
      }

      this.handlers.onMessage(data);
    };

    this.ws.onerror = () => {
      // onclose always follows; handle the reconnect there.
    };

    this.ws.onclose = (event) => {
      this.stopWatchdog();
      if (this.closedByUs) return;

      // 1008 is our policy-violation close: the token is missing, invalid, or
      // the user may not view this event. Retrying cannot fix that.
      if (event.code === 1008) {
        this.setState("offline");
        this.handlers.onMessage({
          type: "unauthorized",
          detail: "You are not authorised to view this auction.",
        });
        return;
      }

      this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.closedByUs) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);

    if (this.attempt >= MAX_ATTEMPTS_BEFORE_OFFLINE) {
      this.setState("offline");
    } else {
      this.setState("reconnecting");
    }

    const delay = this.nextDelay();
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  /**
   * Detects a half-open socket: the TCP connection looks fine but nothing is
   * arriving. Without this the screen silently freezes mid-auction.
   */
  private startWatchdog() {
    this.stopWatchdog();
    this.watchdogTimer = setInterval(() => {
      if (Date.now() - this.lastMessageAt < SILENCE_TIMEOUT_MS) return;
      // Force the close handler to run and reconnect.
      try {
        this.ws?.close();
      } catch {
        /* ignore */
      }
    }, 5_000);
  }

  private stopWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  /** Ask the server for a full snapshot (used after a detected gap). */
  resync() {
    this.send({ type: "resync" });
  }

  close() {
    this.closedByUs = true;
    this.stopWatchdog();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setState("offline");
  }
}

/** Backwards-compatible helper for the simple call sites. */
export function connectAuctionSocket(
  eventId: number,
  token: string,
  onMessage: (data: any) => void,
  onStateChange?: (state: ConnectionState) => void
): AuctionSocket {
  const socket = new AuctionSocket(eventId, token, { onMessage, onStateChange });
  socket.connect();
  return socket;
}

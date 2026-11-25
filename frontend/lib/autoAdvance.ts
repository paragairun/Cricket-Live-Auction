/**
 * Auto-advance decision — when the next player should come up on its own.
 *
 * Kept here, outside the component, because the rule has enough branches to be
 * worth testing directly. The React effect only schedules the timer; this
 * function decides whether the call should actually go out when it fires.
 *
 * New in 2.1.4.
 */

export type AutoAdvanceInput = {
  /** The auctioneer's toggle. Off means manual control throughout. */
  enabled: boolean;
  /** Event status as the store reports it. */
  status: string | null | undefined;
  /** The player currently on the block, or null when it is clear. */
  activePlayerId: number | null | undefined;
  /** Truthy while a Right-to-Match decision is outstanding. */
  rtm?: unknown;
};

/**
 * Only fire when the auction is running, the block is genuinely clear, and no
 * Right-to-Match decision is waiting on a team. Pulling a player up mid-RTM
 * would bury a decision the room still owes an answer to.
 */
export function shouldAutoAdvance(input: AutoAdvanceInput): boolean {
  if (!input.enabled) return false;
  if (input.status !== "active") return false;
  if (input.activePlayerId !== null && input.activePlayerId !== undefined) return false;
  if (input.rtm) return false;
  return true;
}

/**
 * How long to wait before bringing the next player up.
 *
 * A sale earns a beat so the room sees the SOLD card land; starting or
 * resuming the auction should feel immediate.
 */
export function autoAdvanceDelayMs(trigger: "sold" | "unsold" | "start"): number {
  return trigger === "start" ? 600 : 2500;
}

/**
 * A live WebSocket receives the hammer outcome and owns the auto-advance
 * timer. Only make the HTTP fallback request when the socket is unavailable;
 * otherwise the button handler and socket timer can advance twice.
 *
 * The auctioneer's toggle remains authoritative: when auto-advance is off,
 * the next player must be selected manually.
 */
export function shouldUseDirectAdvanceFallback(input: {
  enabled: boolean;
  connection: string | null | undefined;
}): boolean {
  return input.enabled && input.connection !== "live";
}

/**
 * Live auction client state.
 *
 * Version: 2.0.0 (2026-11-20)
 *
 * Changed from 1.x:
 *  - `current_bidder_id` (a captain's user id) is now `current_bid_team_id`.
 *    Bids belong to teams; captains are roster players who do not bid.
 *  - The countdown is derived from an absolute `deadlineEpoch` plus the server
 *    clock offset, instead of a `timer` integer pushed once a second. No drift,
 *    and a client that reconnects mid-player shows the right number at once.
 *  - Each player carries `version`, which goes back to the server with the next
 *    bid so a stale price is rejected rather than silently overwriting.
 *  - `lastSeq` tracks broadcast sequence numbers so a gap can be detected.
 */
import { create } from "zustand";

export interface BidSlab {
  id: number;
  min_value: number;
  max_value: number;
  increment: number;
  sort_order: number;
}

export interface PlayerCategory {
  id: number;
  name: string;
  base_price: number;
  sort_order: number;
  auction_first: boolean;
}

export interface TeamState {
  id: number;
  name: string;
  color: string;
  logo?: string | null;
  captain_id: number | null;
  captain_name?: string | null;
  budget: number;
  spent: number;
  remaining: number;
  max_players: number;
  player_count: number;
}

export interface PlayerState {
  id: number; // auction_player id
  player_id: number;
  name?: string;
  profile_photo?: string | null;
  base_price: number;
  category_id?: number | null;
  current_bid: number;
  current_bid_team_id: number | null;
  status: "pending" | "active" | "queued" | "sold" | "unsold";
  version: number;
}

export interface AuctionTiming {
  base_seconds: number;
  soft_close_threshold_seconds: number;
  soft_close_extension_seconds: number;
}

export interface AuctionRules {
  team_budget: number;
  team_max_players: number;
  team_min_players: number;
  player_base_price: number;
  auction_mode?: "random" | "nomination";
  rtm_enabled?: boolean;
  rtm_cards_per_team?: number;
  exclude_captains_from_auction?: boolean;
  auto_reauction_unsold?: boolean;
}

export type ConnectionState = "connecting" | "live" | "reconnecting" | "offline";

/** A pending Right-to-Match negotiation. New in 2.1.0. */
export interface RtmState {
  auction_player_id: number;
  player_id: number;
  player_name?: string | null;
  /** offered -> holder decides; raise_offered -> bidder may raise; match_offered -> holder must match */
  stage: "offered" | "raise_offered" | "match_offered" | null;
  rights_team_id: number | null;
  rights_team_name?: string | null;
  top_team_id: number | null;
  top_team_name?: string | null;
  top_amount: number | null;
  version: number;
}

export interface Nomination {
  id: number;
  auction_player_id: number;
  player_id?: number | null;
  player_name?: string | null;
  team_id: number;
  team_name?: string | null;
  created_at?: string | null;
}

interface AuctionState {
  eventId: number | null;
  status: string;
  activePlayerId: number | null;
  noTimer: boolean;

  /** Absolute server time (unix seconds) the hammer falls. */
  deadlineEpoch: number | null;
  /** serverTime - clientTime, so countdowns are immune to a wrong local clock. */
  clockOffset: number;

  teams: TeamState[];
  players: PlayerState[];
  bidSlabs: BidSlab[];
  categories: PlayerCategory[];
  timing: AuctionTiming | null;
  rules: AuctionRules | null;

  connection: ConnectionState;
  lastSeq: number;
  missedMessages: boolean;

  /** Non-null while a Right-to-Match decision is outstanding. New in 2.1.0. */
  rtm: RtmState | null;
  nominations: Nomination[];

  setFullState: (payload: any) => void;
  setRtm: (rtm: RtmState | null) => void;
  setNominations: (nominations: Nomination[]) => void;
  setConnection: (state: ConnectionState) => void;
  noteSeq: (seq?: number) => void;
  clearMissed: () => void;

  applyBid: (
    auctionPlayerId: number,
    amount: number,
    teamId: number | null,
    version?: number,
    deadlineEpoch?: number | null,
    serverTime?: number
  ) => void;
  setActivePlayer: (
    auctionPlayerId: number,
    basePrice: number,
    version: number,
    deadlineEpoch: number | null,
    serverTime?: number
  ) => void;
  markPlayerSold: (auctionPlayerId: number, teamId: number | null, price: number) => void;
  markPlayerUnsold: (auctionPlayerId: number) => void;
  clearActivePlayer: () => void;
}

/** Seconds left on the clock, or null when no countdown is running. */
export function remainingSeconds(
  deadlineEpoch: number | null,
  clockOffset: number
): number | null {
  if (deadlineEpoch == null) return null;
  const nowServer = Date.now() / 1000 + clockOffset;
  return Math.max(0, deadlineEpoch - nowServer);
}

export const useAuctionStore = create<AuctionState>((set, get) => ({
  eventId: null,
  status: "draft",
  activePlayerId: null,
  noTimer: false,
  deadlineEpoch: null,
  clockOffset: 0,
  teams: [],
  players: [],
  bidSlabs: [],
  categories: [],
  timing: null,
  rules: null,
  connection: "connecting",
  lastSeq: 0,
  missedMessages: false,
  rtm: null,
  nominations: [],

  setFullState: (payload) =>
    set((prev) => ({
      eventId: payload.event_id ?? prev.eventId,
      status: payload.status ?? prev.status,
      activePlayerId:
        payload.active_auction_player_id !== undefined
          ? payload.active_auction_player_id
          : prev.activePlayerId,
      noTimer: payload.no_timer ?? prev.noTimer,
      deadlineEpoch:
        payload.deadline_epoch !== undefined ? payload.deadline_epoch : prev.deadlineEpoch,
      clockOffset: payload.server_time
        ? payload.server_time - Date.now() / 1000
        : prev.clockOffset,
      teams: payload.teams ?? prev.teams,
      players: payload.players ?? prev.players,
      bidSlabs: payload.bid_slabs ?? prev.bidSlabs,
      categories: payload.categories ?? prev.categories,
      timing: payload.timing ?? prev.timing,
      rules: payload.rules ?? prev.rules,
      rtm: payload.rtm !== undefined ? payload.rtm : prev.rtm,
      nominations: payload.nominations ?? prev.nominations,
      // A full snapshot closes any gap.
      lastSeq: payload.seq ?? prev.lastSeq,
      missedMessages: false,
    })),

  // No-op when unchanged. Zustand notifies every subscriber on every set(),
  // so a component that subscribes to the whole store gets a new reference
  // each time — and if that reference is in a useEffect dependency list, the
  // effect re-runs, reconnects, sets the state again, and loops forever
  // (React error #185). The dependency lists are fixed too, but guarding here
  // means a future one cannot resurrect this.
  setConnection: (connection) => {
    if (get().connection === connection) return;
    set({ connection });
  },

  setRtm: (rtm) => set({ rtm }),

  setNominations: (nominations) => set({ nominations }),

  noteSeq: (seq) => {
    if (seq == null) return;
    const { lastSeq } = get();
    // Non-consecutive sequence means at least one broadcast was missed.
    const gap = lastSeq > 0 && seq > lastSeq + 1;
    set({ lastSeq: Math.max(seq, lastSeq), missedMessages: gap });
  },

  clearMissed: () => set({ missedMessages: false }),

  applyBid: (auctionPlayerId, amount, teamId, version, deadlineEpoch, serverTime) =>
    set((prev) => ({
      players: prev.players.map((p) =>
        p.id === auctionPlayerId
          ? {
              ...p,
              current_bid: amount,
              current_bid_team_id: teamId,
              version: version ?? p.version + 1,
            }
          : p
      ),
      deadlineEpoch: deadlineEpoch !== undefined ? deadlineEpoch : prev.deadlineEpoch,
      clockOffset: serverTime ? serverTime - Date.now() / 1000 : prev.clockOffset,
    })),

  setActivePlayer: (auctionPlayerId, basePrice, version, deadlineEpoch, serverTime) =>
    set((prev) => ({
      activePlayerId: auctionPlayerId,
      deadlineEpoch,
      clockOffset: serverTime ? serverTime - Date.now() / 1000 : prev.clockOffset,
      players: prev.players.map((p) =>
        p.id === auctionPlayerId
          ? {
              ...p,
              base_price: basePrice || p.base_price,
              status: "active" as const,
              current_bid: basePrice || p.base_price,
              current_bid_team_id: null,
              version,
            }
          : p
      ),
    })),

  markPlayerSold: (auctionPlayerId, teamId, price) =>
    set((prev) => {
      // WebSocket delivery can be repeated around reconnects or snapshot
      // refreshes. A sale is a one-time roster mutation: never add its price
      // or player count twice.
      const player = prev.players.find((p) => p.id === auctionPlayerId);
      if (!player || player.status === "sold") return prev;

      return {
        activePlayerId: prev.activePlayerId === auctionPlayerId ? null : prev.activePlayerId,
        deadlineEpoch: null,
        rtm: null,
        players: prev.players.map((p) =>
          p.id === auctionPlayerId
            ? {
                ...p,
                status: "sold" as const,
                current_bid: price,
                current_bid_team_id: teamId,
              }
            : p
        ),
        teams: prev.teams.map((t) =>
          t.id === teamId
            ? {
                ...t,
                spent: t.spent + price,
                remaining: t.budget - (t.spent + price),
                player_count: t.player_count + 1,
              }
            : t
        ),
      };
    }),

  markPlayerUnsold: (auctionPlayerId) =>
    set((prev) => ({
      activePlayerId: prev.activePlayerId === auctionPlayerId ? null : prev.activePlayerId,
      deadlineEpoch: null,
      rtm: null,
      players: prev.players.map((p) =>
        p.id === auctionPlayerId ? { ...p, status: "unsold" as const } : p
      ),
    })),

  clearActivePlayer: () => set({ activePlayerId: null, deadlineEpoch: null }),
}));

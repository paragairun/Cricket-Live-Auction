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
  captain_id: number | null;
  budget: number;
  spent: number;
  max_players: number;
  player_count: number;
}

export interface PlayerState {
  id: number;
  player_id: number;
  base_price: number;
  category_id?: number | null;
  current_bid: number;
  current_bidder_id: number | null;
  status: "pending" | "active" | "sold" | "unsold";
}

interface AuctionState {
  eventId: number | null;
  status: string;
  timer: number;
  activePlayerId: number | null;
  teams: TeamState[];
  players: PlayerState[];
  bidSlabs: BidSlab[];
  categories: PlayerCategory[];

  setFullState: (
    state: Partial<AuctionState> & { bid_slabs?: BidSlab[]; categories?: PlayerCategory[] }
  ) => void;
  updateBid: (auctionPlayerId: number, amount: number, captainId: number | null) => void;
  setTimer: (remaining: number) => void;
  markPlayerSold: (auctionPlayerId: number, captainId: number, price: number) => void;
  markPlayerUnsold: (auctionPlayerId: number) => void;
  setActivePlayer: (auctionPlayerId: number, basePrice: number) => void;
}

export const useAuctionStore = create<AuctionState>((set) => ({
  eventId: null,
  status: "draft",
  timer: 0,
  activePlayerId: null,
  teams: [],
  players: [],
  bidSlabs: [],
  categories: [],

  setFullState: (state) =>
    set((prev) => ({
      ...prev,
      ...state,
      bidSlabs: state.bid_slabs ?? state.bidSlabs ?? prev.bidSlabs,
      categories: state.categories ?? state.categories ?? prev.categories,
    })),

  updateBid: (auctionPlayerId, amount, captainId) =>
    set((prev) => ({
      players: prev.players.map((p) =>
        p.id === auctionPlayerId
          ? { ...p, current_bid: amount, current_bidder_id: captainId }
          : p
      ),
    })),

  setTimer: (remaining) => set({ timer: remaining }),

  markPlayerSold: (auctionPlayerId, captainId, price) =>
    set((prev) => ({
      players: prev.players.map((p) =>
        p.id === auctionPlayerId
          ? { ...p, status: "sold" as const, current_bid: price, current_bidder_id: captainId }
          : p
      ),
      teams: prev.teams.map((t) =>
        t.captain_id === captainId
          ? { ...t, spent: t.spent + price, player_count: t.player_count + 1 }
          : t
      ),
    })),

  markPlayerUnsold: (auctionPlayerId) =>
    set((prev) => ({
      players: prev.players.map((p) =>
        p.id === auctionPlayerId ? { ...p, status: "unsold" as const } : p
      ),
    })),

  setActivePlayer: (auctionPlayerId, basePrice) =>
    set((prev) => ({
      activePlayerId: auctionPlayerId,
      players: prev.players.map((p) => {
        if (p.id !== auctionPlayerId) return p;
        const cat = prev.categories.find((c) => c.id === p.category_id);
        const resolvedBase = cat && cat.base_price > 0 ? cat.base_price : basePrice || p.base_price;
        return {
          ...p,
          base_price: resolvedBase,
          status: "active" as const,
          current_bid: resolvedBase,
          current_bidder_id: null,
        };
      }),
    })),
}));

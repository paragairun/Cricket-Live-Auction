"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import api from "@/lib/api";
import { useAuctionStore, BidSlab, PlayerCategory } from "@/store/auction";
import { AuctionSocket } from "@/lib/ws";

interface PlayerDetail {
  player_id: number;
  name: string;
  profile_photo?: string;
  batting_rating?: number;
  bowling_rating?: number;
  fielding_rating?: number;
  role?: string;
  age?: number;
}

export default function AuctioneerPage() {
  const router = useRouter();
  const { eventId } = useParams<{ eventId: string }>();
  const eid = parseInt(eventId);
  const store = useAuctionStore();

  const [hasAccess, setHasAccess] = useState(false);
  const [checkingAccess, setCheckingAccess] = useState(true);
  const [, setSocket] = useState<AuctionSocket | null>(null);
  const [eventName, setEventName] = useState("Live Player Auction");
  const [eventLogo, setEventLogo] = useState<string | null>(null);
  const [status, setStatus] = useState("draft");
  const [playerDetails, setPlayerDetails] = useState<Record<number, PlayerDetail>>({});
  const [teamRosters, setTeamRosters] = useState<Record<number, { player_id: number; sold_price: number }[]>>({});
  const [viewerCount, setViewerCount] = useState<number>(0);

  const [selectedCaptainId, setSelectedCaptainId] = useState<number | null>(null);
  const [showEditBidModal, setShowEditBidModal] = useState(false);
  const [editBidInput, setEditBidInput] = useState("");
  const [activeDrawerTab, setActiveDrawerTab] = useState<"none" | "sold" | "unsold" | "available" | "teams">("none");
  const [selectedCategoryFilter, setSelectedCategoryFilter] = useState<number | "all">("all");
  const [inspectTeamId, setInspectTeamId] = useState<number | null>(null);
  const [busyAction, setBusyAction] = useState(false);

  const syncState = useCallback(async () => {
    const [{ data }, eventRes, teamRes, viewerStatsRes] = await Promise.all([
      api.get(`/auction/events/${eid}/state`),
      api.get(`/auction/events/${eid}`).catch(() => ({ data: null })),
      api.get(`/auction/events/${eid}/teams`).catch(() => ({ data: [] })),
      api.get(`/auction/events/${eid}/viewer-stats`).catch(() => ({ data: null })),
    ]);

    const resolvedSlabs =
      data.bid_slabs && data.bid_slabs.length > 0
        ? data.bid_slabs
        : eventRes.data?.bid_slabs || [];

    const resolvedCategories =
      data.categories && data.categories.length > 0
        ? data.categories
        : eventRes.data?.categories || [];

    store.setFullState({
      eventId: eid,
      status: data.status,
      timer: data.timer || 0,
      activePlayerId: data.active_player_id,
      teams: data.teams || [],
      players: data.players || [],
      bid_slabs: resolvedSlabs,
      categories: resolvedCategories,
    });

    setStatus(data.status);
    if (teamRes.data) {
      const map: Record<number, { player_id: number; sold_price: number }[]> = {};
      teamRes.data.forEach((t: { id: number; players: { player_id: number; sold_price: number }[] }) => {
        map[t.id] = t.players || [];
      });
      setTeamRosters(map);
    }
    if (viewerStatsRes.data) {
      const count =
        data.status === "completed"
          ? viewerStatsRes.data.total_unique_viewers
          : viewerStatsRes.data.live_viewers;
      setViewerCount(count || 0);
    }
  }, [eid, store]);

  useEffect(() => {
    const checkAccess = async () => {
      try {
        const token = localStorage.getItem("token");
        if (!token) {
          router.replace("/auth/login");
          return;
        }
        const [meRes, eventRes] = await Promise.all([
          api.get("/auth/me"),
          api.get(`/auction/events/${eid}`),
        ]);
        const me = meRes.data as { id: number; roles: string[] };
        const event = eventRes.data as { auctioneer_id: number | null; name: string; logo?: string | null };

        const isAuctioneerRole = (me.roles || []).includes("auctioneer");
        const isAssignedAuctioneer = event.auctioneer_id === me.id;
        if (!isAuctioneerRole || !isAssignedAuctioneer) {
          router.replace(`/auction/${eid}/spectate`);
          return;
        }
        setEventName(event.name || "Live Player Auction");
        setEventLogo(event.logo || null);
        setHasAccess(true);
      } catch {
        router.replace("/dashboard");
      } finally {
        setCheckingAccess(false);
      }
    };
    checkAccess();
  }, [eid, router]);

  useEffect(() => {
    if (!hasAccess) return;
    syncState();
  }, [eid, hasAccess, syncState]);

  useEffect(() => {
    if (!hasAccess || status === "completed") return;
    const token = localStorage.getItem("token") || "";
    const ws = new AuctionSocket(eid, token);
    ws.connect();

    ws.on("*", (msg) => {
      if (msg.type === "timer_tick") store.setTimer(msg.remaining as number);
      if (msg.type === "new_bid")
        store.updateBid(
          msg.auction_player_id as number,
          msg.amount as number,
          (msg.captain_id as number) ?? null
        );
      if (msg.type === "player_sold") {
        store.markPlayerSold(
          msg.auction_player_id as number,
          msg.sold_to_captain_id as number,
          msg.sold_price as number
        );
        syncState();
      }
      if (msg.type === "player_unsold") {
        store.markPlayerUnsold(msg.auction_player_id as number);
        syncState();
      }
      if (msg.type === "player_up") {
        store.setActivePlayer(msg.auction_player_id as number, msg.base_price as number);
        setSelectedCaptainId(null);
      }
      if (msg.type === "auction_paused") setStatus("paused");
      if (msg.type === "auction_resumed") setStatus("active");
      if (msg.type === "auction_completed") setStatus("completed");
      if (msg.type === "viewer_count") setViewerCount(msg.count as number);
      if (msg.status && Array.isArray(msg.players) && Array.isArray(msg.teams)) {
        syncState();
      }
    });

    setSocket(ws);
    return () => ws.disconnect();
  }, [eid, hasAccess, status, syncState, store]);

  useEffect(() => {
    const fetchPlayersInfo = async () => {
      const { data } = await api.get(`/auction/events/${eid}/players-info`).catch(() => ({ data: [] }));
      const map: Record<number, PlayerDetail> = {};
      data.forEach((row: PlayerDetail) => {
        map[row.player_id] = row;
      });
      setPlayerDetails(map);
    };
    fetchPlayersInfo();
  }, [eid]);

  const activeAP = store.players.find((p) => p.id === store.activePlayerId);
  const displayActiveAP = activeAP?.status === "active" ? activeAP : null;

  const captainIds = useMemo(
    () => new Set(store.teams.map((t) => t.captain_id).filter((id): id is number => id !== null)),
    [store.teams]
  );

  const pendingPlayers = useMemo(
    () => store.players.filter((p) => p.status === "pending" && !captainIds.has(p.player_id)),
    [store.players, captainIds]
  );
  const soldPlayers = useMemo(() => store.players.filter((p) => p.status === "sold"), [store.players]);
  const unsoldPlayers = useMemo(() => store.players.filter((p) => p.status === "unsold"), [store.players]);

  const getCategoryObj = useCallback(
    (categoryId?: number | null): PlayerCategory | undefined => {
      if (!categoryId) return undefined;
      return (store.categories || []).find((c) => c.id === categoryId);
    },
    [store.categories]
  );

  const getEffectiveBasePrice = useCallback(
    (player: { base_price: number; category_id?: number | null }) => {
      if (player.category_id) {
        const cat = getCategoryObj(player.category_id);
        if (cat && cat.base_price > 0) return cat.base_price;
      }
      return player.base_price;
    },
    [getCategoryObj]
  );

  const getBidIncrement = useCallback(
    (currentBid: number): number => {
      const slabs: BidSlab[] = store.bidSlabs || [];
      if (slabs.length === 0) return 10000;
      const sorted = [...slabs].sort((a, b) => Number(b.min_value) - Number(a.min_value));
      for (const slab of sorted) {
        const minVal = Number(slab.min_value);
        const maxVal = Number(slab.max_value);
        const inRange = currentBid >= minVal && (maxVal === 0 || currentBid <= maxVal);
        if (inRange) return Number(slab.increment);
      }
      return Number(sorted[sorted.length - 1]?.increment) || 10000;
    },
    [store.bidSlabs]
  );

  const minEventBasePrice = useMemo(() => {
    const prices = [
      ...(store.categories || []).map((c) => c.base_price),
      ...store.players.map((p) => getEffectiveBasePrice(p)),
    ].filter((v) => v > 0);
    return prices.length > 0 ? Math.min(...prices) : 1000;
  }, [store.categories, store.players, getEffectiveBasePrice]);

  const getTeamMaxBid = useCallback(
    (team: { budget: number; spent: number; max_players: number; player_count: number }) => {
      const remainingPurse = Math.max(0, team.budget - team.spent);
      const slotsLeft = Math.max(0, team.max_players - team.player_count);
      if (slotsLeft <= 0) return 0;
      const reserveForOtherSlots = Math.max(0, slotsLeft - 1) * minEventBasePrice;
      return Math.max(0, remainingPurse - reserveForOtherSlots);
    },
    [minEventBasePrice]
  );

  const formatMoney = (val: number) => val.toLocaleString("en-IN");

  const startAuction = () => api.post(`/auction/events/${eid}/start`).then(syncState);
  const pauseAuction = () => api.post(`/auction/events/${eid}/pause`).then(syncState);

  const handleResetMock = async () => {
    if (!confirm("Reset Mock Auction? This clears all bids and resets team purses.")) return;
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/reset-mock-auction`);
      setSelectedCaptainId(null);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Failed to reset mock auction");
    } finally {
      setBusyAction(false);
    }
  };

  const handleRandomNext = async () => {
    if (status !== "active") {
      alert("Start or resume the auction first.");
      return;
    }
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/next-player`, { player_id: null });
      setSelectedCaptainId(null);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "No pending players available");
    } finally {
      setBusyAction(false);
    }
  };

  const handlePickPlayer = async (auctionPlayerId: number) => {
    if (status !== "active") {
      alert("Start or resume the auction first.");
      return;
    }
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/next-player`, { player_id: auctionPlayerId });
      setSelectedCaptainId(null);
      setActiveDrawerTab("none");
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Error selecting player");
    } finally {
      setBusyAction(false);
    }
  };

  const placeBidForCaptain = async (captainId: number, amount: number) => {
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/bid`, {
        amount,
        captain_id: captainId,
      });
      setSelectedCaptainId(captainId);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Error placing bid");
    } finally {
      setBusyAction(false);
    }
  };

  const handleBidUp = async () => {
    if (!displayActiveAP) return;
    const targetCaptain =
      selectedCaptainId ||
      displayActiveAP.current_bidder_id ||
      store.teams.find((t) => t.captain_id)?.captain_id;
    if (!targetCaptain) {
      alert("No teams with captains available to bid.");
      return;
    }
    const isOpening = !displayActiveAP.current_bidder_id;
    const activeBase = getEffectiveBasePrice(displayActiveAP);
    const referenceBid = isOpening ? activeBase : displayActiveAP.current_bid;
    const increment = getBidIncrement(referenceBid);

    const nextAmount =
      isOpening && displayActiveAP.current_bid < activeBase
        ? activeBase
        : referenceBid + increment;

    await placeBidForCaptain(targetCaptain, nextAmount);
  };

  const handleBidDown = async () => {
    if (!displayActiveAP || !displayActiveAP.current_bidder_id) return;
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/bid-down`);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Cannot revert bid further");
    } finally {
      setBusyAction(false);
    }
  };

  const handleOpenEditBid = () => {
    if (!displayActiveAP) return;
    const isOpening = !displayActiveAP.current_bidder_id;
    setEditBidInput(
      String(isOpening ? getEffectiveBasePrice(displayActiveAP) : displayActiveAP.current_bid)
    );
    setShowEditBidModal(true);
  };

  const handleSaveEditBid = async () => {
    const val = parseInt(editBidInput, 10);
    if (isNaN(val) || val < 0) {
      alert("Enter a valid positive amount");
      return;
    }
    setBusyAction(true);
    try {
      await api.patch(`/auction/events/${eid}/bid-edit`, { amount: val });
      setShowEditBidModal(false);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Error updating bid");
    } finally {
      setBusyAction(false);
    }
  };

  const handleSoldOrUnsold = async (markUnsold: boolean) => {
    if (!displayActiveAP) return;
    if (!markUnsold && !displayActiveAP.current_bidder_id) {
      alert("No team has placed a bid yet. Click UNSOLD or tap a team card first.");
      return;
    }
    if (markUnsold && displayActiveAP.current_bidder_id) {
      const ok = confirm("A team currently holds a bid. Are you sure you want to mark this player UNSOLD?");
      if (!ok) return;
    }
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/hammer`);
      setSelectedCaptainId(null);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Error finalizing player");
    } finally {
      setBusyAction(false);
    }
  };

  const reAuctionAllUnsold = async () => {
    if (!confirm("Move all unsold players back to the Available pool?")) return;
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/re-auction-all-unsold`);
      await syncState();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Error re-auctioning unsold players");
    } finally {
      setBusyAction(false);
    }
  };

  const finishAuction = async () => {
    if (pendingPlayers.length > 0) {
      alert("All available players must be auctioned before finishing.");
      return;
    }
    if (!confirm("Complete and lock this auction permanently?")) return;
    await api.post(`/auction/events/${eid}/finish`);
    await syncState();
  };

  if (checkingAccess) {
    return (
      <div className="min-h-screen bg-[#140708] flex items-center justify-center text-amber-200 font-semibold">
        Loading Auction Console...
      </div>
    );
  }

  if (!hasAccess) return null;

  const activeInfo = displayActiveAP ? playerDetails[displayActiveAP.player_id] : undefined;
  const activeCategory = displayActiveAP ? getCategoryObj(displayActiveAP.category_id) : undefined;
  const isOpeningBid = !!displayActiveAP && !displayActiveAP.current_bidder_id;
  const activeBasePrice = displayActiveAP ? getEffectiveBasePrice(displayActiveAP) : 0;
  const displayedCurrentBid = displayActiveAP
    ? isOpeningBid
      ? activeBasePrice
      : displayActiveAP.current_bid
    : 0;

  const currentIncrement = displayActiveAP
    ? getBidIncrement(isOpeningBid ? activeBasePrice : displayActiveAP.current_bid)
    : 0;

  const nextBidAmount = displayActiveAP
    ? isOpeningBid && displayActiveAP.current_bid < activeBasePrice
      ? activeBasePrice
      : (isOpeningBid ? activeBasePrice : displayActiveAP.current_bid) + currentIncrement
    : 0;

  const leadingTeam = displayActiveAP?.current_bidder_id
    ? store.teams.find((t) => t.captain_id === displayActiveAP.current_bidder_id)
    : undefined;

  const filteredPending =
    selectedCategoryFilter === "all"
      ? pendingPlayers
      : pendingPlayers.filter((p) => p.category_id === selectedCategoryFilter);

  const inspectedTeam = store.teams.find((t) => t.id === inspectTeamId);

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#3b070c] via-[#5e0b13] to-[#290408] text-white flex flex-col justify-between select-none">
      {/* ── TOP BROADCAST HEADER BAR ── */}
      <header className="bg-[#7a0c15] border-b border-red-400/20 px-4 py-2.5 shadow-lg">
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => router.push("/dashboard")}
              className="w-9 h-9 rounded-lg bg-black/25 hover:bg-black/40 flex items-center justify-center text-white font-bold transition"
              title="Exit to Dashboard"
            >
              ‹
            </button>
            {eventLogo ? (
              <img src={eventLogo} alt="" className="w-9 h-9 rounded-lg object-cover bg-white/10" />
            ) : (
              <div className="w-9 h-9 rounded-lg bg-amber-400 text-red-950 font-black flex items-center justify-center text-sm shadow">
                🏏
              </div>
            )}
            <div className="min-w-0">
              <h1 className="text-base sm:text-lg font-extrabold tracking-tight truncate">
                {eventName}
              </h1>
              <div className="flex items-center gap-2 text-[11px] text-red-200">
                <span className="inline-flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                  {viewerCount} Live
                </span>
                <span>•</span>
                <span className="uppercase font-bold text-amber-300">{status}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {status !== "completed" && (
              <>
                <button
                  onClick={handleResetMock}
                  disabled={busyAction}
                  className="bg-black/30 hover:bg-black/50 border border-amber-400/40 text-amber-300 px-2.5 py-1.5 rounded-lg text-xs font-bold transition"
                  title="Reset Mock Auction"
                >
                  ↻ Reset
                </button>
                {status !== "active" ? (
                  <button
                    onClick={startAuction}
                    className="bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-3.5 py-1.5 rounded-lg text-xs font-extrabold shadow transition"
                  >
                    ▶ {status === "paused" ? "Resume" : "Start"}
                  </button>
                ) : (
                  <button
                    onClick={pauseAuction}
                    className="bg-amber-400 hover:bg-amber-300 text-slate-950 px-3 py-1.5 rounded-lg text-xs font-extrabold shadow transition"
                  >
                    ⏸ Pause
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </header>

      {/* ── MAIN BROADCAST STAGE ── */}
      <main className="flex-1 max-w-5xl w-full mx-auto px-4 py-4 flex flex-col justify-between gap-4">
        {/* 1. HERO PLAYER BANNER */}
        {displayActiveAP ? (
          <div className="relative rounded-2xl bg-gradient-to-br from-[#8c111b] to-[#57080f] border border-red-300/20 p-4 sm:p-5 shadow-2xl">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <div className="flex items-center gap-2">
                <span className="bg-amber-400 text-red-950 text-[11px] font-black uppercase px-2.5 py-0.5 rounded-full">
                  {activeCategory ? activeCategory.name : "Standard Pool"}
                </span>
                <span className="text-xs text-red-100/90 font-semibold">
                  Base: ₹{formatMoney(activeBasePrice)}
                </span>
              </div>
              <span className="text-xs font-bold text-amber-300 bg-black/30 px-2.5 py-0.5 rounded-full">
                {isOpeningBid
                  ? `Opening Bid: ₹${formatMoney(nextBidAmount)}`
                  : `Slab Step: +₹${formatMoney(currentIncrement)}`}
              </span>
            </div>

            <div className="flex items-center gap-4 sm:gap-6">
              <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-full border-4 border-white/85 bg-slate-800 overflow-hidden shrink-0 shadow-xl flex items-center justify-center">
                {activeInfo?.profile_photo ? (
                  <img
                    src={activeInfo.profile_photo}
                    alt={activeInfo.name}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <span className="text-3xl font-black text-amber-300">
                    {(activeInfo?.name || "P")[0]?.toUpperCase()}
                  </span>
                )}
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2.5">
                  <span className="text-xl sm:text-2xl font-black text-amber-300 pr-2.5 border-r-2 border-white/30">
                    #{displayActiveAP.player_id}
                  </span>
                  <h2 className="text-xl sm:text-3xl font-black tracking-tight truncate">
                    {activeInfo?.name || `Player #${displayActiveAP.player_id}`}
                  </h2>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm text-red-100/90 font-medium">
                  {activeInfo?.role && <span>{activeInfo.role}</span>}
                  {activeInfo?.age && <span>• {activeInfo.age} Yrs</span>}
                  {(activeInfo?.batting_rating || activeInfo?.bowling_rating) && (
                    <span>
                      • Bat {activeInfo.batting_rating ?? "-"} / Bowl {activeInfo.bowling_rating ?? "-"}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* 2. CENTRAL CURRENT BID BAR + PENCIL EDIT */}
            <div className="mt-4 pt-3 border-t border-white/15 flex items-center justify-between gap-3">
              <div className="w-10" />
              <div className="text-center">
                <div className="inline-flex items-center gap-2">
                  <span className="w-6 h-6 rounded-full bg-amber-400 text-amber-950 font-black text-xs flex items-center justify-center shadow">
                    ₹
                  </span>
                  <span className="text-3xl sm:text-4xl font-black tracking-tight text-white drop-shadow">
                    {formatMoney(displayedCurrentBid)}
                  </span>
                </div>
                <p className="text-xs font-bold mt-0.5 text-amber-300">
                  {leadingTeam
                    ? `Current Highest: ${leadingTeam.name}`
                    : "Awaiting Opening Bid"}
                </p>
              </div>
              <button
                onClick={handleOpenEditBid}
                disabled={status !== "active"}
                className="w-10 h-10 rounded-xl bg-white/15 hover:bg-white/25 disabled:opacity-40 flex items-center justify-center text-white transition shadow"
                title="Edit Current Bid Amount"
              >
                ✎
              </button>
            </div>
          </div>
        ) : (
          <div className="rounded-2xl bg-black/30 border border-white/15 py-12 px-6 text-center">
            <p className="text-lg font-bold text-amber-200">
              {status === "active"
                ? "No Player on the Block"
                : status === "completed"
                ? "Auction Completed"
                : "Auction Paused / Not Started"}
            </p>
            <p className="text-xs text-red-200/80 mt-1">
              {status === "active"
                ? "Tap RANDOM or MANUAL below to bring up the next player."
                : "Click Start/Resume in the top right to begin."}
            </p>
          </div>
        )}

        {/* 3. TEAM PADDLE CARDS GRID */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[11px] font-bold uppercase tracking-wider text-red-200/80">
              Team Paddles (Tap Team to Bid {displayActiveAP ? `₹${formatMoney(nextBidAmount)}` : ""})
            </span>
            {store.bidSlabs.length > 0 && (
              <span className="text-[11px] text-amber-300/90">
                {store.bidSlabs.length} Bid Slabs Active
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {store.teams.map((team) => {
              const remainingPurse = Math.max(0, team.budget - team.spent);
              const maxBid = getTeamMaxBid(team);
              const isLeading =
                !!displayActiveAP && displayActiveAP.current_bidder_id === team.captain_id;
              const isSelected = selectedCaptainId === team.captain_id;
              const cannotAfford = !!displayActiveAP && maxBid < nextBidAmount;
              const isFull = team.player_count >= team.max_players;
              const disabled =
                status !== "active" ||
                !displayActiveAP ||
                !team.captain_id ||
                isLeading ||
                cannotAfford ||
                isFull ||
                busyAction;

              return (
                <div
                  key={team.id}
                  onClick={() => {
                    if (!disabled && team.captain_id) {
                      placeBidForCaptain(team.captain_id, nextBidAmount);
                    }
                  }}
                  className={`rounded-xl bg-white text-slate-900 p-2.5 shadow-md transition relative flex items-center gap-3 ${
                    isLeading
                      ? "ring-4 ring-emerald-400 bg-emerald-50"
                      : isSelected
                      ? "ring-4 ring-amber-400"
                      : disabled
                      ? "opacity-60 cursor-not-allowed"
                      : "cursor-pointer hover:scale-[1.01] hover:ring-2 hover:ring-amber-300"
                  }`}
                >
                  <div className="flex flex-col items-center shrink-0">
                    <div
                      className="w-11 h-11 rounded-full flex items-center justify-center font-black text-sm text-white shadow relative"
                      style={{ backgroundColor: team.color || "#16a34a" }}
                    >
                      {team.name
                        .split(" ")
                        .map((w) => w[0])
                        .join("")
                        .slice(0, 2)
                        .toUpperCase()}
                      <span className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-red-600 text-white text-[10px] font-bold flex items-center justify-center border-2 border-white">
                        {team.player_count}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setInspectTeamId(team.id);
                      }}
                      className="mt-1 bg-red-700 hover:bg-red-800 text-white text-[9px] font-bold px-2 py-0.5 rounded"
                    >
                      View Squad
                    </button>
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-1">
                      <p className="text-xs font-extrabold text-slate-900 truncate">
                        {team.name}
                      </p>
                      {isLeading && (
                        <span className="text-[9px] font-black uppercase bg-emerald-600 text-white px-1.5 py-0.5 rounded">
                          Highest
                        </span>
                      )}
                    </div>

                    <div className="mt-1 flex items-center gap-1 text-xs font-bold text-slate-700">
                      <span className="text-amber-500">●</span>
                      <span>
                        {formatMoney(remainingPurse)} / {formatMoney(team.budget)}
                      </span>
                    </div>

                    <div className="mt-0.5 flex items-center justify-between text-[11px]">
                      <span className="text-slate-500 font-semibold">
                        Max Bid:{" "}
                        <strong className={cannotAfford ? "text-red-600" : "text-slate-800"}>
                          {formatMoney(maxBid)}
                        </strong>
                      </span>
                      <span className="text-slate-400 font-semibold">
                        {team.player_count}/{team.max_players}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 4. BROADCAST ACTION DOCK */}
        {status !== "completed" && (
          <div className="rounded-2xl bg-black/40 border border-white/15 p-3 shadow-xl">
            <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-stretch">
              <div className="md:col-span-4 rounded-xl bg-[#540910] border border-red-400/25 p-2.5 flex flex-col justify-between">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-extrabold text-white">New Player</span>
                  <button
                    onClick={() =>
                      setActiveDrawerTab(activeDrawerTab === "available" ? "none" : "available")
                    }
                    className="text-[11px] text-amber-300 hover:underline font-bold"
                  >
                    Filter ({pendingPlayers.length})
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={handleRandomNext}
                    disabled={status !== "active" || busyAction || pendingPlayers.length === 0}
                    className="bg-[#7c121c] hover:bg-[#961723] disabled:opacity-40 border border-white/20 rounded-lg py-2 px-2 text-xs font-extrabold tracking-wide transition"
                  >
                    🎲 RANDOM
                  </button>
                  <button
                    onClick={() =>
                      setActiveDrawerTab(activeDrawerTab === "available" ? "none" : "available")
                    }
                    disabled={status !== "active"}
                    className="bg-[#7c121c] hover:bg-[#961723] disabled:opacity-40 border border-white/20 rounded-lg py-2 px-2 text-xs font-extrabold tracking-wide transition"
                  >
                    📋 MANUAL
                  </button>
                </div>
              </div>

              <div className="md:col-span-8 grid grid-cols-2 gap-2.5">
                <button
                  onClick={handleBidUp}
                  disabled={status !== "active" || !displayActiveAP || busyAction}
                  className="rounded-xl bg-[#facc15] hover:bg-[#fde047] disabled:opacity-40 text-slate-950 font-black text-sm sm:text-base py-3 px-3 shadow-md flex items-center justify-center gap-1.5 transition"
                >
                  <span>↑</span>
                  <span>
                    BID UP {displayActiveAP ? `(+${formatMoney(currentIncrement)})` : ""}
                  </span>
                </button>

                <button
                  onClick={handleBidDown}
                  disabled={
                    status !== "active" ||
                    !displayActiveAP ||
                    !displayActiveAP.current_bidder_id ||
                    busyAction
                  }
                  className="rounded-xl bg-[#facc15] hover:bg-[#fde047] disabled:opacity-40 text-slate-950 font-black text-sm sm:text-base py-3 px-3 shadow-md flex items-center justify-center gap-1.5 transition"
                >
                  <span>↓</span>
                  <span>DOWN (UNDO)</span>
                </button>

                <button
                  onClick={() => handleSoldOrUnsold(false)}
                  disabled={
                    status !== "active" ||
                    !displayActiveAP ||
                    !displayActiveAP.current_bidder_id ||
                    busyAction
                  }
                  className="rounded-xl bg-[#65a30d] hover:bg-[#84cc16] disabled:opacity-40 text-white font-black text-sm sm:text-base py-3 px-3 shadow-md transition"
                >
                  SOLD
                </button>

                <button
                  onClick={() => handleSoldOrUnsold(true)}
                  disabled={status !== "active" || !displayActiveAP || busyAction}
                  className="rounded-xl bg-[#ef4444] hover:bg-[#f87171] disabled:opacity-40 text-white font-black text-sm sm:text-base py-3 px-3 shadow-md transition"
                >
                  UNSOLD
                </button>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* ── BOTTOM STATUS BAR ── */}
      <footer className="bg-[#1b0608] border-t border-white/15 px-3 py-2.5">
        <div className="max-w-5xl mx-auto grid grid-cols-4 gap-2">
          <button
            onClick={() => setActiveDrawerTab(activeDrawerTab === "sold" ? "none" : "sold")}
            className={`py-2 px-2 rounded-lg text-xs sm:text-sm font-extrabold transition ${
              activeDrawerTab === "sold"
                ? "bg-emerald-500 text-slate-950"
                : "bg-[#4d7c0f] hover:bg-[#65a30d] text-white"
            }`}
          >
            Sold {soldPlayers.length}
          </button>

          <button
            onClick={() => setActiveDrawerTab(activeDrawerTab === "unsold" ? "none" : "unsold")}
            className={`py-2 px-2 rounded-lg text-xs sm:text-sm font-extrabold transition ${
              activeDrawerTab === "unsold"
                ? "bg-red-400 text-slate-950"
                : "bg-[#dc2626] hover:bg-[#ef4444] text-white"
            }`}
          >
            Unsold {unsoldPlayers.length}
          </button>

          <button
            onClick={() => setActiveDrawerTab(activeDrawerTab === "available" ? "none" : "available")}
            className={`py-2 px-2 rounded-lg text-xs sm:text-sm font-extrabold transition ${
              activeDrawerTab === "available"
                ? "bg-amber-300 text-slate-950"
                : "bg-[#ca8a04] hover:bg-[#eab308] text-white"
            }`}
          >
            Available {pendingPlayers.length}
          </button>

          <button
            onClick={() => setActiveDrawerTab(activeDrawerTab === "teams" ? "none" : "teams")}
            className={`py-2 px-2 rounded-lg text-xs sm:text-sm font-extrabold transition ${
              activeDrawerTab === "teams"
                ? "bg-teal-300 text-slate-950"
                : "bg-[#0d9488] hover:bg-[#14b8a6] text-white"
            }`}
          >
            Teams {store.teams.length}
          </button>
        </div>
      </footer>

      {/* ── DRAWER FOR SOLD / UNSOLD / AVAILABLE / TEAMS ── */}
      {activeDrawerTab !== "none" && (
        <div className="fixed inset-0 z-40 bg-black/70 flex items-end sm:items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full max-h-[80vh] flex flex-col overflow-hidden shadow-2xl">
            <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
              <h3 className="font-extrabold text-base uppercase tracking-wide text-amber-300">
                {activeDrawerTab === "sold" && `Sold Players (${soldPlayers.length})`}
                {activeDrawerTab === "unsold" && `Unsold Players (${unsoldPlayers.length})`}
                {activeDrawerTab === "available" && `Available Players (${filteredPending.length})`}
                {activeDrawerTab === "teams" && `Teams Summary (${store.teams.length})`}
              </h3>
              <div className="flex items-center gap-2">
                {activeDrawerTab === "unsold" &&
                  unsoldPlayers.length > 0 &&
                  pendingPlayers.length === 0 &&
                  status === "active" && (
                    <button
                      onClick={reAuctionAllUnsold}
                      className="bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-bold px-2.5 py-1 rounded"
                    >
                      Re-Auction All Unsold
                    </button>
                  )}
                {activeDrawerTab === "available" &&
                  pendingPlayers.length === 0 &&
                  !displayActiveAP &&
                  status !== "completed" && (
                    <button
                      onClick={finishAuction}
                      className="bg-red-500 hover:bg-red-400 text-white text-xs font-bold px-2.5 py-1 rounded"
                    >
                      Finish Auction
                    </button>
                  )}
                <button
                  onClick={() => setActiveDrawerTab("none")}
                  className="text-slate-400 hover:text-white text-lg font-bold px-2"
                >
                  ✕
                </button>
              </div>
            </div>

            {activeDrawerTab === "available" && store.categories.length > 0 && (
              <div className="px-4 py-2 bg-slate-950 border-b border-slate-800 flex flex-wrap gap-1.5">
                <button
                  onClick={() => setSelectedCategoryFilter("all")}
                  className={`px-2.5 py-1 rounded-full text-xs font-bold ${
                    selectedCategoryFilter === "all"
                      ? "bg-amber-400 text-slate-950"
                      : "bg-slate-800 text-slate-300"
                  }`}
                >
                  All ({pendingPlayers.length})
                </button>
                {store.categories.map((cat) => {
                  const count = pendingPlayers.filter((p) => p.category_id === cat.id).length;
                  return (
                    <button
                      key={cat.id}
                      onClick={() => setSelectedCategoryFilter(cat.id)}
                      className={`px-2.5 py-1 rounded-full text-xs font-bold ${
                        selectedCategoryFilter === cat.id
                          ? "bg-amber-400 text-slate-950"
                          : "bg-slate-800 text-slate-300"
                      }`}
                    >
                      {cat.name} ({count})
                    </button>
                  );
                })}
              </div>
            )}

            <div className="p-4 overflow-y-auto space-y-2 flex-1">
              {activeDrawerTab === "available" &&
                (filteredPending.length === 0 ? (
                  <p className="text-center text-slate-500 text-sm py-8">No available players.</p>
                ) : (
                  filteredPending.map((p) => {
                    const info = playerDetails[p.player_id];
                    const cat = getCategoryObj(p.category_id);
                    const effBase = getEffectiveBasePrice(p);
                    return (
                      <div
                        key={p.id}
                        className="flex items-center justify-between bg-slate-800/80 border border-slate-700 rounded-xl px-3 py-2.5"
                      >
                        <div>
                          <p className="font-bold text-sm text-white">
                            #{p.player_id} {info?.name || `Player #${p.player_id}`}
                          </p>
                          <p className="text-xs text-slate-400">
                            Base: ₹{formatMoney(effBase)}
                            {cat ? ` • ${cat.name}` : ""}
                          </p>
                        </div>
                        <button
                          onClick={() => handlePickPlayer(p.id)}
                          disabled={status !== "active" || busyAction}
                          className="bg-amber-400 hover:bg-amber-300 disabled:opacity-40 text-slate-950 font-extrabold text-xs px-3 py-1.5 rounded-lg"
                        >
                          Bring to Block
                        </button>
                      </div>
                    );
                  })
                ))}

              {activeDrawerTab === "sold" &&
                (soldPlayers.length === 0 ? (
                  <p className="text-center text-slate-500 text-sm py-8">No players sold yet.</p>
                ) : (
                  soldPlayers.map((p) => {
                    const info = playerDetails[p.player_id];
                    const buyer = store.teams.find((t) => t.captain_id === p.current_bidder_id);
                    return (
                      <div
                        key={p.id}
                        className="flex items-center justify-between bg-slate-800/80 border border-emerald-500/30 rounded-xl px-3 py-2.5"
                      >
                        <div>
                          <p className="font-bold text-sm text-white">
                            {info?.name || `Player #${p.player_id}`}
                          </p>
                          <p className="text-xs text-emerald-400">
                            Sold to {buyer?.name || "Team"}
                          </p>
                        </div>
                        <span className="font-black text-sm text-emerald-300">
                          ₹{formatMoney(p.current_bid)}
                        </span>
                      </div>
                    );
                  })
                ))}

              {activeDrawerTab === "unsold" &&
                (unsoldPlayers.length === 0 ? (
                  <p className="text-center text-slate-500 text-sm py-8">No unsold players.</p>
                ) : (
                  unsoldPlayers.map((p) => {
                    const info = playerDetails[p.player_id];
                    const effBase = getEffectiveBasePrice(p);
                    return (
                      <div
                        key={p.id}
                        className="flex items-center justify-between bg-slate-800/80 border border-red-500/30 rounded-xl px-3 py-2.5"
                      >
                        <div>
                          <p className="font-bold text-sm text-white">
                            {info?.name || `Player #${p.player_id}`}
                          </p>
                          <p className="text-xs text-slate-400">
                            Base: ₹{formatMoney(effBase)}
                          </p>
                        </div>
                        {status === "active" && !displayActiveAP && (
                          <button
                            onClick={() => handlePickPlayer(p.id)}
                            className="bg-amber-400 hover:bg-amber-300 text-slate-950 font-extrabold text-xs px-3 py-1.5 rounded-lg"
                          >
                            Re-Auction
                          </button>
                        )}
                      </div>
                    );
                  })
                ))}

              {activeDrawerTab === "teams" &&
                store.teams.map((t) => (
                  <div
                    key={t.id}
                    className="flex items-center justify-between bg-slate-800/80 border border-slate-700 rounded-xl px-3 py-2.5"
                  >
                    <div>
                      <p className="font-bold text-sm text-white">{t.name}</p>
                      <p className="text-xs text-slate-400">
                        Purse Left: ₹{formatMoney(t.budget - t.spent)} • Players: {t.player_count}/{t.max_players}
                      </p>
                    </div>
                    <button
                      onClick={() => setInspectTeamId(t.id)}
                      className="bg-slate-700 hover:bg-slate-600 text-white text-xs font-bold px-3 py-1.5 rounded-lg"
                    >
                      View Squad
                    </button>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}

      {/* ── PENCIL EDIT BID MODAL ── */}
      {showEditBidModal && displayActiveAP && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-sm w-full p-5 shadow-2xl">
            <h3 className="text-base font-extrabold text-white mb-1">Edit Current Bid</h3>
            <p className="text-xs text-slate-400 mb-4">
              Manually adjust the active bid amount for{" "}
              <strong className="text-amber-300">
                {activeInfo?.name || `Player #${displayActiveAP.player_id}`}
              </strong>
            </p>
            <input
              type="number"
              value={editBidInput}
              onChange={(e) => setEditBidInput(e.target.value)}
              className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2.5 text-lg font-black text-white mb-4 focus:outline-none focus:border-amber-400"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowEditBidModal(false)}
                className="px-4 py-2 rounded-lg bg-slate-800 text-slate-300 text-xs font-bold"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveEditBid}
                disabled={busyAction}
                className="px-4 py-2 rounded-lg bg-amber-400 hover:bg-amber-300 text-slate-950 text-xs font-extrabold"
              >
                Save Amount
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── TEAM SQUAD INSPECTOR MODAL ── */}
      {inspectedTeam && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-5 shadow-2xl">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-base font-extrabold text-white">{inspectedTeam.name}</h3>
                <p className="text-xs text-slate-400">
                  Purse Left: ₹{formatMoney(inspectedTeam.budget - inspectedTeam.spent)} • Max Bid: ₹
                  {formatMoney(getTeamMaxBid(inspectedTeam))}
                </p>
              </div>
              <button
                onClick={() => setInspectTeamId(null)}
                className="text-slate-400 hover:text-white font-bold text-lg"
              >
                ✕
              </button>
            </div>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {(teamRosters[inspectedTeam.id] || []).length === 0 ? (
                <p className="text-xs text-slate-500 py-6 text-center">No players bought yet.</p>
              ) : (
                (teamRosters[inspectedTeam.id] || []).map((tp, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between bg-slate-800 rounded-lg px-3 py-2 text-xs"
                  >
                    <span className="font-bold text-white">
                      {playerDetails[tp.player_id]?.name || `Player #${tp.player_id}`}
                    </span>
                    <span className="font-extrabold text-amber-300">
                      ₹{formatMoney(tp.sold_price)}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
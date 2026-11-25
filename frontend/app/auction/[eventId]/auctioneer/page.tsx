"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import api, { describeError, newIdempotencyKey, postIdempotent } from "@/lib/api";
import {
  useAuctionStore,
  PlayerCategory,
  remainingSeconds,
} from "@/store/auction";
import { AuctionSocket } from "@/lib/ws";
import { shouldAutoAdvance, autoAdvanceDelayMs } from "@/lib/autoAdvance";
import { useFitText } from "@/lib/useFitText";
import { incrementFor } from "@/lib/bidRules";

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
  const [eventName, setEventName] = useState("EVENT NAME");
  const [eventLogo, setEventLogo] = useState<string | null>(null);
  const [backgroundImage, setBackgroundImage] = useState<string | null>(null);
  const [status, setStatus] = useState("draft");
  const [playerDetails, setPlayerDetails] = useState<Record<number, PlayerDetail>>({});
  const [teamRosters, setTeamRosters] = useState<
    Record<number, { player_id: number; sold_price: number }[]>
  >({});

  const [selectedTeamId, setSelectedTeamId] = useState<number | null>(null);
  const [, setSecondsLeft] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rtmRaiseAmount, setRtmRaiseAmount] = useState("");
  const [autoAdvance, setAutoAdvance] = useState(true);
  const autoAdvanceRef = useRef(true);
  const advanceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const advancingRef = useRef(false);
  const [justSoldPlayerId, setJustSoldPlayerId] = useState<number | null>(null);
  const [showEditBidModal, setShowEditBidModal] = useState(false);
  const [editBidInput, setEditBidInput] = useState("");
  const [activeDrawerTab, setActiveDrawerTab] = useState<
    "none" | "sold" | "unsold" | "available" | "teams" | "squads"
  >("none");
  const [selectedCategoryFilter, setSelectedCategoryFilter] = useState<number | "all">("all");
  const [inspectTeamId, setInspectTeamId] = useState<number | null>(null);
  const [selectedSquadTeamId, setSelectedSquadTeamId] = useState<number | null>(null);
  const [busyAction, setBusyAction] = useState(false);
  const [noBidCount, setNoBidCount] = useState(0);

  const syncState = useCallback(async () => {
    const [{ data }, teamRes] = await Promise.all([
      api.get(`/auction/events/${eid}/state`),
      api.get(`/auction/events/${eid}/teams`).catch(() => ({ data: [] })),
    ]);

    useAuctionStore.getState().setFullState(data);

    setStatus(data.status);
    if (teamRes.data) {
      const map: Record<number, { player_id: number; sold_price: number }[]> = {};
      teamRes.data.forEach(
        (t: { id: number; players: { player_id: number; sold_price: number }[] }) => {
          map[t.id] = t.players || [];
        }
      );
      setTeamRosters(map);
    }
  }, [eid]);

  const handleRandomNext = useCallback(
    async (opts: { silent?: boolean; fast?: boolean } = {}) => {
      if (advancingRef.current) return;
      advancingRef.current = true;
      if (!opts.silent) setBusyAction(true);
      try {
        await postIdempotent(`/auction/events/${eid}/next-player`, {
          auction_player_id: null,
        });
        setSelectedTeamId(null);
        setJustSoldPlayerId(null);
        const { data } = await api.get(`/auction/events/${eid}/state`);
        useAuctionStore.getState().setFullState(data);
        setStatus(data.status);
      } catch (e: unknown) {
        const info = describeError(e);
        if (!opts.silent) setActionError(info.message);
        await syncState().catch(() => {});
      } finally {
        advancingRef.current = false;
        if (!opts.silent) setBusyAction(false);
      }
    },
    [eid, syncState]
  );

  const scheduleAutoAdvance = useCallback(
    (trigger: "sold" | "unsold" | "start") => {
      if (!autoAdvanceRef.current) return;
      if (advanceTimerRef.current) clearTimeout(advanceTimerRef.current);
      const baseDelay = autoAdvanceDelayMs(trigger);
      const delay =
        trigger === "start" ? Math.min(baseDelay, 500) : Math.min(baseDelay, 100);
      advanceTimerRef.current = setTimeout(() => {
        advanceTimerRef.current = null;
        const fresh = useAuctionStore.getState();
        if (
          !shouldAutoAdvance({
            enabled: autoAdvanceRef.current,
            status: fresh.status,
            activePlayerId: fresh.activePlayerId,
            rtm: fresh.rtm,
          })
        ) {
          return;
        }
        void handleRandomNext({ silent: true, fast: true });
      }, delay);
    },
    [handleRandomNext]
  );

  useEffect(() => {
    autoAdvanceRef.current = autoAdvance;
  }, [autoAdvance]);

  useEffect(
    () => () => {
      if (advanceTimerRef.current) clearTimeout(advanceTimerRef.current);
    },
    []
  );

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
        const event = eventRes.data as {
          auctioneer_id: number | null;
          organizer_id: number | null;
          admin_id: number | null;
          name: string;
          logo?: string | null;
          background_image?: string | null;
          status?: string;
        };

        const isOperator =
          event.auctioneer_id === me.id ||
          event.organizer_id === me.id ||
          event.admin_id === me.id ||
          (me.roles || []).includes("admin");
        if (!isOperator) {
          router.replace(`/auction/${eid}/spectate`);
          return;
        }
        if (event.status === "draft" || event.status === "completed") {
          router.replace(`/auction/${eid}/spectate`);
          return;
        }
        setEventName(event.name || "EVENT NAME");
        setEventLogo(event.logo || null);
        setBackgroundImage(event.background_image || null);
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
    const ws = new AuctionSocket(eid, token, {
      onStateChange: (state) => useAuctionStore.getState().setConnection(state),
      onMessage: (msg) => {
        useAuctionStore.getState().noteSeq(msg.seq);

        switch (msg.type) {
          case "auction_state":
          case "auction_reset":
            setJustSoldPlayerId(null);
            useAuctionStore.getState().setFullState(msg);
            setStatus(msg.status);
            break;

          case "new_bid":
            setJustSoldPlayerId(null);
            useAuctionStore.getState().applyBid(
              msg.auction_player_id,
              msg.amount,
              msg.team_id ?? null,
              msg.version,
              msg.deadline_epoch,
              msg.server_time
            );
            break;

          case "bid_reverted":
          case "bid_corrected":
            useAuctionStore.getState().applyBid(
              msg.auction_player_id,
              msg.current_bid,
              msg.current_bid_team_id ?? null,
              msg.version
            );
            break;

          case "rtm_offered":
          case "rtm_exercised":
          case "rtm_raised":
            useAuctionStore.getState().setRtm({
              auction_player_id: msg.auction_player_id,
              player_id: msg.player_id,
              player_name: msg.player_name,
              stage: msg.stage,
              rights_team_id: msg.rights_team_id ?? null,
              rights_team_name: msg.rights_team_name ?? null,
              top_team_id: msg.top_team_id ?? null,
              top_team_name: msg.top_team_name ?? null,
              top_amount: msg.top_amount ?? null,
              version: msg.version,
            });
            syncState();
            break;

          case "player_nominated":
          case "nomination_withdrawn":
            syncState();
            break;

          case "player_up":
            setJustSoldPlayerId(null);
            useAuctionStore.getState().setActivePlayer(
              msg.auction_player_id,
              msg.base_price,
              msg.version,
              msg.deadline_epoch ?? null,
              msg.server_time
            );
            setSelectedTeamId(null);
            break;

          case "player_sold":
            setJustSoldPlayerId(msg.auction_player_id);
            useAuctionStore.getState().markPlayerSold(
              msg.auction_player_id,
              msg.sold_to_team_id ?? null,
              msg.sold_price
            );
            setSelectedTeamId(null);
            scheduleAutoAdvance("sold");
            break;

          case "player_unsold":
            setJustSoldPlayerId(null);
            useAuctionStore.getState().markPlayerUnsold(msg.auction_player_id);
            setSelectedTeamId(null);
            scheduleAutoAdvance("unsold");
            break;

          case "player_pulled":
            setJustSoldPlayerId(null);
            useAuctionStore.getState().markPlayerUnsold(msg.auction_player_id);
            setSelectedTeamId(null);
            break;

          case "auction_paused":
            setStatus("paused");
            break;
          case "auction_resumed":
          case "auction_started":
            setStatus("active");
            break;
          case "auction_completed":
            setStatus("completed");
            break;
          case "viewer_count":
            break;
          case "unauthorized":
            router.replace(`/auction/${eid}/spectate`);
            break;
          default:
            break;
        }
      },
    });
    ws.connect();

    setSocket(ws);
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eid, hasAccess, scheduleAutoAdvance]);

  useEffect(() => {
    const fetchPlayersInfo = async () => {
      const { data } = await api
        .get(`/auction/events/${eid}/players-info`)
        .catch(() => ({ data: [] }));
      const map: Record<number, PlayerDetail> = {};
      data.forEach((row: PlayerDetail) => {
        map[row.player_id] = row;
      });
      setPlayerDetails(map);
    };
    fetchPlayersInfo();
  }, [eid]);

  const activeAP = store.players.find((p) => p.id === store.activePlayerId);
  const displayActiveAP =
    activeAP && (activeAP.status === "active" || justSoldPlayerId === activeAP.id)
      ? activeAP
      : null;

  const isPlayerSold =
    !!displayActiveAP &&
    (displayActiveAP.status === "sold" || justSoldPlayerId === displayActiveAP.id);

  const captainIds = useMemo(
    () =>
      new Set(
        store.teams.map((t) => t.captain_id).filter((id): id is number => id !== null)
      ),
    [store.teams]
  );

  const excludeCaptains = store.rules?.exclude_captains_from_auction ?? true;
  const pendingPlayers = useMemo(
    () =>
      store.players.filter(
        (p) => p.status === "pending" && (!excludeCaptains || !captainIds.has(p.player_id))
      ),
    [store.players, captainIds, excludeCaptains]
  );
  const soldPlayers = useMemo(
    () => store.players.filter((p) => p.status === "sold"),
    [store.players]
  );
  const unsoldPlayers = useMemo(
    () => store.players.filter((p) => p.status === "unsold"),
    [store.players]
  );

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
    (currentBid: number): number => incrementFor(store.bidSlabs || [], currentBid),
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
    (team: {
      budget: number;
      spent: number;
      max_players: number;
      player_count: number;
    }) => {
      const remainingPurse = Math.max(0, team.budget - team.spent);
      const slotsLeft = Math.max(0, team.max_players - team.player_count);
      if (slotsLeft <= 0) return 0;
      const minSquad = store.rules?.team_min_players ?? 0;
      const reserveSlots =
        minSquad > 0 ? Math.max(0, minSquad - team.player_count - 1) : 0;
      const reserve = reserveSlots * minEventBasePrice;
      return Math.max(0, remainingPurse - reserve);
    },
    [minEventBasePrice, store.rules]
  );

  useEffect(() => {
    if (store.deadlineEpoch == null) {
      setSecondsLeft(null);
      return;
    }
    const tick = () => {
      const left = remainingSeconds(store.deadlineEpoch, store.clockOffset);
      setSecondsLeft(left == null ? null : Math.ceil(left));
    };
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [store.deadlineEpoch, store.clockOffset]);

  const rtmAction = async (path: string, body?: Record<string, unknown>) => {
    setBusyAction(true);
    setActionError(null);
    try {
      await api.post(`/auction/events/${eid}/rtm/${path}`, body ?? {});
      await syncState();
    } catch (e: unknown) {
      setActionError(describeError(e).message);
      await syncState();
    } finally {
      setBusyAction(false);
    }
  };

  const formatMoney = (val: number) => val.toLocaleString("en-IN");

  const startAuction = async () => {
    setBusyAction(true);
    setActionError(null);
    try {
      await api.post(`/auction/events/${eid}/start`);
      setStatus("active");
      void syncState().catch(() => {});
    } catch (e: unknown) {
      setActionError(describeError(e).message || "Failed to start auction");
    } finally {
      setBusyAction(false);
    }
  };

  const pauseAuction = async () => {
    await api.post(`/auction/events/${eid}/pause`);
    setStatus("paused");
  };

  const handleResetMock = async () => {
    if (
      !confirm(
        "Reset the auction? This clears every bid, roster and purse. It cannot be undone."
      )
    )
      return;
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/reset`);
      setSelectedTeamId(null);
      setJustSoldPlayerId(null);
      await syncState();
    } catch (e: unknown) {
      setActionError(describeError(e).message);
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
      await postIdempotent(`/auction/events/${eid}/next-player`, {
        auction_player_id: auctionPlayerId,
      });
      setSelectedTeamId(null);
      setJustSoldPlayerId(null);
      setActiveDrawerTab("none");
      const { data } = await api.get(`/auction/events/${eid}/state`);
      useAuctionStore.getState().setFullState(data);
    } catch (e: unknown) {
      alert(describeError(e).message || "Error selecting player");
    } finally {
      setBusyAction(false);
    }
  };

  const placeBidForTeam = async (teamId: number, amount: number) => {
    if (!displayActiveAP) return;
    setBusyAction(true);
    setActionError(null);
    try {
      await api.post(`/auction/events/${eid}/bid`, {
        team_id: teamId,
        amount,
        expected_version: displayActiveAP.version,
        idempotency_key: newIdempotencyKey(),
      });
      setSelectedTeamId(teamId);
      useAuctionStore
        .getState()
        .applyBid(displayActiveAP.id, amount, teamId, displayActiveAP.version + 1);
    } catch (e: unknown) {
      const info = describeError(e);
      setActionError(
        info.conflict
          ? `${info.message} (the other console bid first — price refreshed)`
          : info.message
      );
      if (info.conflict) await syncState().catch(() => {});
    } finally {
      setBusyAction(false);
    }
  };

  const handleBidUp = async () => {
    if (!displayActiveAP) return;
    const isOpening = !displayActiveAP.current_bid_team_id;
    const activeBase = getEffectiveBasePrice(displayActiveAP);
    const referenceBid =
      displayActiveAP.current_bid > 0
        ? displayActiveAP.current_bid
        : isOpening
        ? activeBase
        : 0;
    const increment = getBidIncrement(referenceBid);
    const nextAmount = referenceBid + increment;

    setActionError(null);
    useAuctionStore.getState().applyBid(
      displayActiveAP.id,
      nextAmount,
      displayActiveAP.current_bid_team_id ?? null,
      displayActiveAP.version + 1
    );

    try {
      await api.patch(`/auction/events/${eid}/bid-edit`, { amount: nextAmount });
    } catch (e: unknown) {
      if (displayActiveAP.current_bid_team_id) {
        setActionError(describeError(e).message);
      }
    }
  };

  const handleBidDown = async () => {
    if (!displayActiveAP) return;
    const activeBase = getEffectiveBasePrice(displayActiveAP);
    const currentVal =
      displayActiveAP.current_bid > 0 ? displayActiveAP.current_bid : activeBase;

    if (currentVal <= activeBase) return;

    const approxPrev = Math.max(activeBase, currentVal - getBidIncrement(currentVal));
    const step = getBidIncrement(approxPrev);
    const nextAmount = Math.max(activeBase, currentVal - step);

    setActionError(null);
    useAuctionStore.getState().applyBid(
      displayActiveAP.id,
      nextAmount,
      displayActiveAP.current_bid_team_id ?? null,
      displayActiveAP.version + 1
    );

    try {
      await api.patch(`/auction/events/${eid}/bid-edit`, { amount: nextAmount });
    } catch (e: unknown) {
      setActionError(describeError(e).message || "Error updating bid");
      await syncState().catch(() => {});
    }
  };

  const handleOpenEditBid = () => {
    if (!displayActiveAP) return;
    const isOpening = !displayActiveAP.current_bid_team_id;
    setEditBidInput(
      String(
        isOpening ? getEffectiveBasePrice(displayActiveAP) : displayActiveAP.current_bid
      )
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
      useAuctionStore.getState().applyBid(
        displayActiveAP!.id,
        val,
        displayActiveAP!.current_bid_team_id ?? null,
        displayActiveAP!.version + 1
      );
    } catch (e: unknown) {
      alert(describeError(e).message || "Error updating bid");
    } finally {
      setBusyAction(false);
    }
  };

  const lotTotal = store.players.length;
  const canAdjustPrice = !!displayActiveAP;

  const handleNoBid = async () => {
    if (!displayActiveAP) return;
    if (displayActiveAP.current_bid_team_id) {
      const ok = confirm(
        "A team currently holds a bid. Are you sure you want to mark this player NO BID?"
      );
      if (!ok) return;
    }
    setBusyAction(true);
    try {
      await api.post(`/auction/events/${eid}/hammer`);
      setSelectedTeamId(null);
      setJustSoldPlayerId(null);
      useAuctionStore.getState().markPlayerUnsold(displayActiveAP.id);
      setNoBidCount((n) => n + 1);
      await handleRandomNext({ silent: true, fast: true });
    } catch (e: unknown) {
      alert(describeError(e).message || "Error marking no bid");
    } finally {
      setBusyAction(false);
    }
  };

  const handleSubmit = async () => {
    if (!displayActiveAP) return;
    setBusyAction(true);
    try {
      const soldTeamId = displayActiveAP.current_bid_team_id ?? null;
      const soldPrice = displayActiveAP.current_bid;
      await api.post(`/auction/events/${eid}/hammer`);
      setSelectedTeamId(null);
      if (soldTeamId) {
        setJustSoldPlayerId(displayActiveAP.id);
        useAuctionStore
          .getState()
          .markPlayerSold(displayActiveAP.id, soldTeamId, soldPrice);
      }
      await handleRandomNext({ silent: true, fast: true });
    } catch (e: unknown) {
      alert(describeError(e).message || "Error submitting");
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
    } catch (e: unknown) {
      alert(describeError(e).message || "Error re-auctioning unsold players");
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

  const activeInfo = displayActiveAP
    ? playerDetails[displayActiveAP.player_id]
    : undefined;
  const activeCategory = displayActiveAP
    ? getCategoryObj(displayActiveAP.category_id)
    : undefined;

  const nameRef = useFitText<HTMLHeadingElement>(16, 36, [
    activeInfo?.name,
    displayActiveAP?.player_id,
  ]);

  if (checkingAccess) {
    return (
      <div className="h-[100dvh] w-full bg-[#000000] flex items-center justify-center text-amber-300 font-bold tracking-wider uppercase text-sm">
        Loading Auctioneer Console...
      </div>
    );
  }

  if (!hasAccess) return null;

  const isOpeningBid = !!displayActiveAP && !displayActiveAP.current_bid_team_id;
  const activeBasePrice = displayActiveAP
    ? getEffectiveBasePrice(displayActiveAP)
    : 0;
  const displayedCurrentBid = displayActiveAP
    ? isOpeningBid
      ? activeBasePrice
      : displayActiveAP.current_bid
    : 0;

  const currentIncrement = displayActiveAP
    ? getBidIncrement(isOpeningBid ? activeBasePrice : displayActiveAP.current_bid)
    : 10000;

  const teamBidAmount = displayActiveAP
    ? isOpeningBid
      ? Math.max(activeBasePrice, displayActiveAP.current_bid || 0)
      : displayActiveAP.current_bid + currentIncrement
    : 0;

  const nextBidAmount = displayActiveAP
    ? isOpeningBid && displayActiveAP.current_bid < activeBasePrice
      ? activeBasePrice
      : (isOpeningBid ? activeBasePrice : displayActiveAP.current_bid) +
        currentIncrement
    : 0;

  const leadingTeam = displayActiveAP?.current_bid_team_id
    ? store.teams.find((t) => t.id === displayActiveAP.current_bid_team_id)
    : undefined;

  const filteredPending =
    selectedCategoryFilter === "all"
      ? pendingPlayers
      : pendingPlayers.filter((p) => p.category_id === selectedCategoryFilter);

  const inspectedTeam = store.teams.find((t) => t.id === inspectTeamId);
  const stripTeams = store.teams.slice(0, 6);

  const resolvedBgUrl = backgroundImage
    ? backgroundImage.startsWith("http") || backgroundImage.startsWith("/")
      ? backgroundImage
      : `/${backgroundImage}`
    : null;

  return (
    <div className="h-[100dvh] w-full overflow-hidden grid grid-rows-[auto_auto_minmax(0,1fr)_auto] bg-[#000000] text-white select-none">
      {/* ── TOP HEADER BAR ── */}
      <header className="bg-[#000000] border-b border-[#262626] px-4 py-2">
        <div className="mx-auto w-full max-w-[1920px] grid grid-cols-1 md:grid-cols-3 items-center gap-2">
          {/* Left: Event logo placeholder + EVENT NAME + READY / LIVE */}
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => router.push("/dashboard")}
              className="w-7 h-7 shrink-0 flex items-center justify-center text-xs font-bold"
              title="Back to Dashboard"
            >
              ‹
            </button>
            <div className="w-12 h-9 shrink-0 flex items-center justify-center overflow-hidden bg-black">
              {eventLogo && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={eventLogo}
                  alt={eventName}
                  className="w-full h-full object-contain"
                />
              )}
            </div>
            <div className="min-w-0">
              <h1 className="text-lg sm:text-xl font-black uppercase tracking-tight text-white truncate leading-none">
                {eventName}
              </h1>
              <div className="text-[9px] font-bold uppercase tracking-widest mt-0.5 leading-none">
                <span
                  className={
                    status === "active" || status === "paused"
                      ? "text-emerald-400"
                      : "text-white/80"
                  }
                >
                  {status === "active" || status === "paused" ? "LIVE" : "READY"}
                </span>
              </div>
            </div>
          </div>

          {/* Center: PLAYERS X / XX + NO BID XX */}
          <div className="flex items-center justify-center gap-8">
            <button
              type="button"
              onClick={() => setActiveDrawerTab("sold")}
              className="text-center group"
              title="View sold players"
            >
              <div className="text-[10px] font-extrabold uppercase tracking-wider text-amber-400 group-hover:underline">
                PLAYERS
              </div>
              <div className="text-sm sm:text-base font-black text-amber-400 tabular-nums leading-tight">
                {soldPlayers.length + unsoldPlayers.length} / {lotTotal || "XX"}
              </div>
            </button>

            <button
              type="button"
              onClick={() => setActiveDrawerTab("unsold")}
              className="text-center group"
              title="View unsold / no-bid players"
            >
              <div className="text-[10px] font-extrabold uppercase tracking-wider text-sky-400 group-hover:underline">
                NO BID
              </div>
              <div className="text-sm sm:text-base font-black text-sky-400 tabular-nums leading-tight">
                {noBidCount > 0 ? noBidCount : unsoldPlayers.length || "XX"}
              </div>
            </button>
          </div>

          {/* Right: RESTART + START / PAUSE */}
          <div className="flex items-center justify-end gap-5">
            {status !== "completed" && (
              <>
                <button
                  type="button"
                  onClick={handleResetMock}
                  disabled={busyAction}
                  className="flex flex-col items-center text-white hover:text-amber-300 disabled:opacity-40 transition"
                >
                  <span className="text-[10px] font-bold uppercase tracking-wider">
                    RESTART
                  </span>
                  <span className="text-base leading-none mt-0.5">↻</span>
                </button>

                {status !== "active" ? (
                  <button
                    type="button"
                    onClick={startAuction}
                    className="flex flex-col items-center text-emerald-400 hover:text-emerald-300 transition"
                  >
                    <span className="text-[10px] font-extrabold uppercase tracking-wider">
                      {status === "paused" ? "RESUME" : "START"}
                    </span>
                    <span className="text-base leading-none mt-0.5">▷</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={pauseAuction}
                    className="flex flex-col items-center text-amber-300 hover:text-amber-200 transition"
                  >
                    <span className="text-[10px] font-extrabold uppercase tracking-wider">
                      PAUSE
                    </span>
                    <span className="text-base leading-none mt-0.5">❚❚</span>
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </header>

      {/* ── ALERTS (RTM & ERRORS) ── */}
      <div>
        {store.rtm && (
          <div className="px-4 py-1.5 bg-blue-950/85 border-b border-blue-400/40">
            <div className="mx-auto w-full max-w-[1920px] flex flex-wrap items-center justify-between gap-2 text-xs">
              <div>
                <strong className="uppercase tracking-wider text-blue-200 mr-2">
                  Right to Match —{" "}
                  {store.rtm.player_name ?? `Player #${store.rtm.player_id}`}
                </strong>
                <span className="text-blue-100">
                  {store.rtm.top_team_name ?? "Top bidder"} bid{" "}
                  <strong>₹{formatMoney(store.rtm.top_amount ?? 0)}</strong> ·
                  Rights: <strong>{store.rtm.rights_team_name ?? "—"}</strong>
                </span>
              </div>

              {store.rtm.stage === "offered" && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => rtmAction("exercise")}
                    disabled={busyAction}
                    className="bg-blue-500 hover:bg-blue-400 text-white font-bold px-2.5 py-1 rounded text-xs"
                  >
                    Exercise RTM
                  </button>
                  <button
                    onClick={() => rtmAction("decline")}
                    disabled={busyAction}
                    className="bg-neutral-800 hover:bg-neutral-700 border border-neutral-600 px-2.5 py-1 rounded text-xs"
                  >
                    Decline
                  </button>
                </div>
              )}

              {store.rtm.stage === "raise_offered" && (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={rtmRaiseAmount}
                    onChange={(e) => setRtmRaiseAmount(e.target.value)}
                    placeholder={`> ${store.rtm.top_amount ?? 0}`}
                    className="bg-neutral-900 border border-neutral-700 rounded px-2 py-1 text-xs w-28"
                  />
                  <button
                    onClick={() => {
                      const amount = parseInt(rtmRaiseAmount, 10);
                      if (!Number.isFinite(amount)) {
                        setActionError("Enter the raised amount");
                        return;
                      }
                      rtmAction("raise", { amount }).then(() =>
                        setRtmRaiseAmount("")
                      );
                    }}
                    disabled={busyAction}
                    className="bg-amber-500 hover:bg-amber-400 text-black font-bold px-2.5 py-1 rounded text-xs"
                  >
                    Raise
                  </button>
                  <button
                    onClick={() => rtmAction("skip-raise")}
                    disabled={busyAction}
                    className="bg-neutral-800 hover:bg-neutral-700 border border-neutral-600 px-2.5 py-1 rounded text-xs"
                  >
                    No Raise
                  </button>
                </div>
              )}

              {store.rtm.stage === "match_offered" && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => rtmAction("match")}
                    disabled={busyAction}
                    className="bg-blue-500 hover:bg-blue-400 text-white font-bold px-2.5 py-1 rounded text-xs"
                  >
                    Match
                  </button>
                  <button
                    onClick={() => rtmAction("no-match")}
                    disabled={busyAction}
                    className="bg-neutral-800 hover:bg-neutral-700 border border-neutral-600 px-2.5 py-1 rounded text-xs"
                  >
                    No Match
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {actionError && (
          <div className="px-4 py-1 bg-red-950/90 border-b border-red-500/50">
            <div className="mx-auto w-full max-w-[1920px] flex items-center justify-between gap-4">
              <p className="text-xs text-red-200">{actionError}</p>
              <button
                onClick={() => setActionError(null)}
                className="text-[11px] font-bold text-red-300 hover:text-white"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── MAIN SPLIT BROADCAST STAGE ── */}
      <main
        className="relative min-h-0 overflow-hidden bg-cover bg-center"
        style={
          resolvedBgUrl
            ? {
                backgroundImage: `url("${resolvedBgUrl}")`,
              }
            : {
                background:
                  "linear-gradient(105deg, #c81008 0%, #d42108 42%, #e85d04 75%, #f48c06 100%)",
              }
        }
      >
        {/* Subtle cricket batsman background silhouette */}
        <svg
          aria-hidden="true"
          viewBox="0 0 400 300"
          className="pointer-events-none absolute right-[18%] bottom-0 h-[85%] w-auto opacity-15 text-black fill-current"
        >
          <circle cx="215" cy="68" r="18" />
          <path d="M198 92 L234 95 L242 155 L265 235 L244 238 L224 172 L205 245 L182 242 L200 158 Z" />
          <path d="M232 105 L292 132 L286 146 L228 122 Z" />
          <rect
            x="288"
            y="130"
            width="12"
            height="92"
            rx="3"
            transform="rotate(24 288 130)"
          />
        </svg>

        {/* Diagonal crimson divider line */}
        <div
          aria-hidden="true"
          className="pointer-events-none hidden lg:block absolute top-0 bottom-0 left-[44%] w-1 bg-[#9e0b05]/80 -skew-x-[10deg]"
        />

        <div className="relative z-10 mx-auto h-full w-full max-w-[1920px] grid grid-cols-1 lg:grid-cols-12 gap-5 px-5 py-3">
          {/* LEFT ZONE: Player Showcase Card (Photo Placeholder) */}
          <div className="lg:col-span-6 min-h-0 flex flex-col justify-center">
            {displayActiveAP ? (
              <div className="bg-[#f4f4f2] text-[#111111] shadow-2xl overflow-hidden flex flex-col max-h-full w-full max-w-[540px] mx-auto">
                {/* Player photo placeholder */}
                <div className="relative flex-1 min-h-0 bg-[#dcdcd8] overflow-hidden">
                  {activeInfo?.profile_photo && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={activeInfo.profile_photo}
                      alt={activeInfo.name || "Player"}
                      className="w-full h-full object-cover object-top"
                    />
                  )}
                </div>

                {/* Metadata + Player Name row */}
                <div className="px-3.5 pt-2 pb-1.5 flex items-end gap-3 border-b border-neutral-300">
                  <div className="shrink-0 flex flex-col items-center gap-0.5 pb-0.5">
                    <span className="bg-[#f59e0b] text-white text-[9px] font-black uppercase tracking-wider px-2 py-0.5 rounded-full">
                      {activeCategory ? activeCategory.name : "EMERALD"}
                    </span>
                    <span className="text-[10px] font-bold text-neutral-600 tabular-nums">
                      #{displayActiveAP.player_id}
                    </span>
                  </div>

                  <div className="min-w-0 flex-1 h-10 flex items-center">
                    <h2
                      ref={nameRef}
                      className="w-full font-black uppercase tracking-tight text-[#111111] leading-none truncate"
                    >
                      {activeInfo?.name || `PLAYER #${displayActiveAP.player_id}`}
                    </h2>
                  </div>
                </div>

                {/* 4-column stats: AGE | BAT | BALL | FIELD */}
                <div className="grid grid-cols-4 px-3.5 py-2 text-center bg-[#f4f4f2]">
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">
                      AGE
                    </div>
                    <div className="text-sm font-black text-[#111111] tabular-nums">
                      {activeInfo?.age ?? "24"}
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">
                      BAT
                    </div>
                    <div className="text-sm font-black text-[#111111] tabular-nums">
                      {activeInfo?.batting_rating ?? "7.5"}
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">
                      BALL
                    </div>
                    <div className="text-sm font-black text-[#111111] tabular-nums">
                      {activeInfo?.bowling_rating ?? "7.5"}
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">
                      FIELD
                    </div>
                    <div className="text-sm font-black text-[#111111] tabular-nums">
                      {activeInfo?.fielding_rating ?? "7.5"}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="bg-black/45 border border-white/20 p-6 text-center max-w-[540px] w-full mx-auto">
                <p className="text-lg font-black uppercase tracking-wide text-amber-300">
                  {status === "active"
                    ? "NO PLAYER ON THE BLOCK"
                    : status === "completed"
                    ? "AUCTION COMPLETED"
                    : "AUCTION READY / PAUSED"}
                </p>
                <p className="text-xs text-white/80 mt-1.5">
                  {status === "active"
                    ? autoAdvance
                      ? "Bringing next player up..."
                      : "Click 'pick manually' on the right to select a player."
                    : "Click START in the top-right header to begin."}
                </p>
              </div>
            )}
          </div>

          {/* RIGHT ZONE: Bidding Controls + Active Bid / Winning Team Banner */}
          <div className="lg:col-span-6 min-h-0 flex flex-col justify-between gap-2 max-w-[540px] w-full mx-auto lg:mx-0">
            {/* Top action buttons row: UP | DOWN | NO BID */}
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={handleBidUp}
                disabled={!canAdjustPrice}
                className="bg-black/90 hover:bg-black disabled:opacity-45 border border-white/25 px-2 py-1.5 text-center transition flex flex-col items-center justify-center"
              >
                <span className="text-sm font-black uppercase tracking-wide text-white leading-tight">
                  ↑ UP
                </span>
                <span className="text-[9px] text-white/75 leading-tight mt-0.5 truncate max-w-full">
                  Increase by ₹ {formatMoney(currentIncrement || 10000)}
                </span>
              </button>

              <button
                type="button"
                onClick={handleBidDown}
                disabled={!canAdjustPrice}
                className="bg-black/90 hover:bg-black disabled:opacity-45 border border-white/25 px-2 py-1.5 text-center transition flex flex-col items-center justify-center"
              >
                <span className="text-sm font-black uppercase tracking-wide text-white leading-tight">
                  ↓ DOWN
                </span>
                <span className="text-[9px] text-white/75 leading-tight mt-0.5 truncate max-w-full">
                  Decrease by ₹ {formatMoney(currentIncrement || 10000)}
                </span>
              </button>

              <button
                type="button"
                onClick={() => handleNoBid()}
                disabled={status !== "active" || !displayActiveAP || busyAction}
                className="bg-black/90 hover:bg-black disabled:opacity-45 border border-white/25 px-2 py-1.5 text-center transition flex flex-col items-center justify-center"
              >
                <span className="text-sm font-black uppercase tracking-wide text-white leading-tight">
                  ⊘ NO BID
                </span>
              </button>
            </div>

            {/* Compact red SUBMIT button + right-aligned pick manually link */}
            <div>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={status !== "active" || !displayActiveAP || busyAction}
                className="w-full bg-[#e61208] hover:bg-[#ff190d] disabled:opacity-45 text-white font-black uppercase tracking-wider text-xs sm:text-sm py-2 shadow-lg transition"
              >
                SUBMIT
              </button>

              <div className="flex items-center justify-between mt-1">
                <label className="inline-flex items-center gap-1.5 text-[10px] text-white/80 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoAdvance}
                    onChange={(e) => setAutoAdvance(e.target.checked)}
                    className="accent-amber-300 w-3 h-3"
                  />
                  <span>Auto-advance</span>
                </label>

                <div className="flex items-center gap-3">
                  {unsoldPlayers.length > 0 && pendingPlayers.length === 0 && status === "active" && !store.rules?.auto_reauction_unsold && (
                    <button type="button" onClick={reAuctionAllUnsold} disabled={busyAction || !!displayActiveAP} className="text-[11px] text-amber-300 underline hover:text-amber-200 disabled:opacity-40">re-auction unsold</button>
                  )}
                  <button type="button" onClick={() => { setSelectedSquadTeamId(store.teams[0]?.id ?? null); setActiveDrawerTab("squads"); }} className="text-[11px] text-white underline hover:text-amber-200 font-medium">view squads</button>
                  <button
                    type="button"
                    onClick={() => setActiveDrawerTab(activeDrawerTab === "available" ? "none" : "available")}
                    className="text-[11px] text-white underline hover:text-amber-200 font-medium"
                  >
                    pick manually
                  </button>
                </div>
              </div>
            </div>

            {/* Two-tier banner: Gold BID FOR / SOLD FOR + Black TEAM NAME / BIDDING TEAM / WINNING TEAM */}
            <div className="flex-1 min-h-0 flex flex-col justify-end">
              <div className="w-full shadow-2xl overflow-hidden">
                {/* Upper tier: Mustard/Gold (#B8860B) */}
                <div className="relative bg-[#b8860b] px-4 py-2.5 text-white">
                  <div className="text-[9px] font-bold uppercase tracking-wider text-white/90">
                    {isPlayerSold ? "SOLD FOR" : "BID FOR"}
                  </div>

                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-3xl sm:text-5xl font-black tracking-tight tabular-nums leading-none">
                      {displayActiveAP
                        ? formatMoney(displayedCurrentBid)
                        : "XX"}
                    </span>
                    <span className="text-sm sm:text-lg font-extrabold uppercase tracking-wide">
                      LAKHS
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={handleOpenEditBid}
                    disabled={!displayActiveAP}
                    title="Edit bid amount manually"
                    className="absolute top-2 right-2 w-7 h-7 flex items-center justify-center text-xs text-white"
                  >
                    ✎
                  </button>
                </div>

                {/* Lower tier: Solid Black (#000000) */}
                <div className="bg-[#000000] px-4 py-2.5 flex items-center gap-3.5">
                  <div className="w-11 h-11 shrink-0 flex items-center justify-center overflow-hidden">
                    {leadingTeam?.logo && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={leadingTeam.logo}
                        alt={leadingTeam.name}
                        className="w-full h-full object-cover"
                      />
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="text-lg sm:text-2xl font-black uppercase tracking-tight text-white truncate leading-tight">
                      {leadingTeam ? leadingTeam.name : "TEAM NAME"}
                    </div>
                    <div className="border-t border-[#333333] mt-1 pt-0.5 text-[9px] font-semibold uppercase tracking-wider text-white/75">
                      {isPlayerSold ? "WINNING TEAM" : "BIDDING TEAM"}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* ── BOTTOM TEAM STRIP (6-COLUMN TEAM SUMMARY BAR) ── */}
      <footer className="bg-[#000000] border-t-2 border-[#8b0000]">
        <div className="mx-auto w-full max-w-[1920px] grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 divide-y sm:divide-y-0 lg:divide-x divide-[#333333]">
          {stripTeams.map((team) => {
            const remainingPurse = Math.max(0, team.budget - team.spent);
            const maxBid = getTeamMaxBid(team);
            const isLeading =
              !!displayActiveAP && displayActiveAP.current_bid_team_id === team.id;
            const isSelected = selectedTeamId === team.id;
            const cannotAfford = !!displayActiveAP && maxBid < teamBidAmount;
            const isFull = team.player_count >= team.max_players;
            const disabled =
              status !== "active" ||
              !displayActiveAP ||
              isLeading ||
              cannotAfford ||
              isFull ||
              busyAction;

            return (
              <div
                key={team.id}
                onClick={() => {
                  if (!disabled) placeBidForTeam(team.id, teamBidAmount);
                }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setInspectTeamId(team.id);
                }}
                title={
                  disabled
                    ? `${team.name} — right-click to view squad`
                    : `Click to bid ₹${formatMoney(nextBidAmount)} for ${team.name}`
                }
                className={`px-2.5 py-2 flex flex-col justify-between transition ${
                  isLeading
                    ? "bg-amber-950/50 ring-1 ring-inset ring-amber-400"
                    : isSelected
                    ? "bg-neutral-900 ring-1 ring-inset ring-amber-300"
                    : disabled
                    ? "opacity-75 cursor-default"
                    : "cursor-pointer hover:bg-neutral-900"
                }`}
              >
                <div className="flex items-start gap-2 min-w-0">
                  <div className="w-8 h-8 shrink-0 flex items-center justify-center overflow-hidden">
                    {team.logo && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={team.logo}
                        alt={team.name}
                        className="w-full h-full object-cover"
                      />
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-bold text-white truncate leading-tight">
                      {team.name}
                    </div>
                    <div className="text-[10px] text-white/75 tabular-nums leading-tight mt-0.5">
                      {team.player_count}/{team.max_players} Players
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-1.5 mt-2 pt-1">
                  <div>
                    <div className="text-[9px] text-white/65 leading-none">
                      Purse
                    </div>
                    <div className="text-[11px] font-bold text-white tabular-nums truncate mt-0.5">
                      ₹ {formatMoney(remainingPurse)}
                    </div>
                  </div>

                  <div>
                    <div className="text-[9px] text-white/65 leading-none">
                      Max Bid
                    </div>
                    <div className="text-[11px] font-bold text-white tabular-nums truncate mt-0.5">
                      ₹ {formatMoney(maxBid)}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </footer>

      {/* ── DRAWER FOR SOLD / UNSOLD / AVAILABLE / TEAMS ── */}
      {activeDrawerTab !== "none" && (
        <div className="fixed inset-0 z-40 bg-black/75 flex items-end sm:items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full max-h-[80vh] flex flex-col overflow-hidden shadow-2xl">
            <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
              <h3 className="font-extrabold text-base uppercase tracking-wide text-amber-300">
                {activeDrawerTab === "sold" &&
                  `Sold Players (${soldPlayers.length})`}
                {activeDrawerTab === "unsold" &&
                  `Unsold Players (${unsoldPlayers.length})`}
                {activeDrawerTab === "available" &&
                  `Available Players (${filteredPending.length})`}
                {activeDrawerTab === "teams" && `Teams Summary (${store.teams.length})`}
                {activeDrawerTab === "squads" && `Team Squads (${store.teams.length})`}
              </h3>
              <div className="flex items-center gap-2">
                {activeDrawerTab === "unsold" &&
                  unsoldPlayers.length > 0 &&
                  pendingPlayers.length === 0 &&
                  status === "active" && (
                    <button
                      onClick={reAuctionAllUnsold}
                      disabled={busyAction}
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
                  const count = pendingPlayers.filter(
                    (p) => p.category_id === cat.id
                  ).length;
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
                  <p className="text-center text-slate-500 text-sm py-8">
                    No available players.
                  </p>
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
                            #{p.player_id}{" "}
                            {info?.name || `Player #${p.player_id}`}
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
                  <p className="text-center text-slate-500 text-sm py-8">
                    No players sold yet.
                  </p>
                ) : (
                  soldPlayers.map((p) => {
                    const info = playerDetails[p.player_id];
                    const buyer = store.teams.find(
                      (t) => t.id === p.current_bid_team_id
                    );
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
                  <p className="text-center text-slate-500 text-sm py-8">
                    No unsold players.
                  </p>
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

              {activeDrawerTab === "squads" && (
                <div className="space-y-3">
                  <div className="flex gap-1.5 overflow-x-auto border-b border-slate-700 pb-2">
                    {store.teams.map((team) => (
                      <button key={team.id} onClick={() => setSelectedSquadTeamId(team.id)} className={`shrink-0 rounded-lg px-3 py-2 text-xs font-bold ${selectedSquadTeamId === team.id ? "bg-amber-400 text-slate-950" : "bg-slate-800 text-slate-300"}`}>
                        {team.name}
                      </button>
                    ))}
                  </div>
                  {(() => {
                    const team = store.teams.find((item) => item.id === selectedSquadTeamId) || store.teams[0];
                    if (!team) return <p className="text-center text-slate-500 py-8">No teams configured.</p>;
                    const roster = teamRosters[team.id] || [];
                    return <div className="space-y-2">
                      <div className="flex justify-between text-xs text-slate-300"><strong>{team.name}</strong><span>{team.player_count}/{team.max_players} players · Purse ₹{formatMoney(team.budget - team.spent)}</span></div>
                      {team.captain_id && <div className="rounded-lg border border-amber-500/30 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">Captain · {team.captain_name || playerDetails[team.captain_id]?.name || `Player #${team.captain_id}`}</div>}
                      {roster.length === 0 ? <p className="text-center text-slate-500 py-6 text-xs">No players bought yet.</p> : roster.map((player) => <div key={player.player_id} className="flex justify-between rounded-lg bg-slate-800 px-3 py-2 text-xs"><span className="text-white font-semibold">{playerDetails[player.player_id]?.name || `Player #${player.player_id}`}</span><strong className="text-amber-300">₹{formatMoney(player.sold_price)}</strong></div>)}
                    </div>;
                  })()}
                </div>
              )}

              {activeDrawerTab === "teams" &&
                store.teams.map((t) => (
                  <div
                    key={t.id}
                    className="flex items-center justify-between bg-slate-800/80 border border-slate-700 rounded-xl px-3 py-2.5"
                  >
                    <div>
                      <p className="font-bold text-sm text-white">{t.name}</p>
                      <p className="text-xs text-slate-400">
                        Purse Left: ₹{formatMoney(t.budget - t.spent)} •
                        Players: {t.player_count}/{t.max_players}
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
            <h3 className="text-base font-extrabold text-white mb-1">
              Edit Current Bid
            </h3>
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
                <h3 className="text-base font-extrabold text-white">
                  {inspectedTeam.name}
                </h3>
                <p className="text-xs text-slate-400">
                  Purse Left: ₹
                  {formatMoney(inspectedTeam.budget - inspectedTeam.spent)} •
                  Max Bid: ₹{formatMoney(getTeamMaxBid(inspectedTeam))}
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
                <p className="text-xs text-slate-500 py-6 text-center">
                  No players bought yet.
                </p>
              ) : (
                (teamRosters[inspectedTeam.id] || []).map((tp, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between bg-slate-800 rounded-lg px-3 py-2 text-xs"
                  >
                    <span className="font-bold text-white">
                      {playerDetails[tp.player_id]?.name ||
                        `Player #${tp.player_id}`}
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
"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { shallow } from "zustand/shallow";
import { useParams, useRouter } from "next/navigation";
import api, { describeError, newIdempotencyKey, postIdempotent } from "@/lib/api";
import { resolveMediaUrl } from "@/lib/mediaUrl";
import {
  useAuctionStore,
  PlayerCategory,
} from "@/store/auction";
import { AuctionSocket } from "@/lib/ws";
import { useFitText } from "@/lib/useFitText";
import { incrementFor } from "@/lib/bidRules";
import { formatAuctionPrice } from "@/lib/formatAuctionPrice";
import { getMinimumAuctionBasePrice, getRemainingAuctionReserveSlots } from "@/lib/auctionPurse";

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
  // Subscribe only to data this page renders. Socket metadata and unrelated
  // store updates should not re-render this large operator console.
  const store = useAuctionStore(
    (state) => ({
      activePlayerId: state.activePlayerId,
      bidSlabs: state.bidSlabs,
      categories: state.categories,
      clockOffset: state.clockOffset,
      deadlineEpoch: state.deadlineEpoch,
      players: state.players,
      rtm: state.rtm,
      rules: state.rules,
      teams: state.teams,
    }),
    shallow
  );

  const [hasAccess, setHasAccess] = useState(false);
  const [checkingAccess, setCheckingAccess] = useState(true);
  const [eventName, setEventName] = useState("EVENT NAME");
  const [eventLogo, setEventLogo] = useState<string | null>(null);
  const [backgroundImage, setBackgroundImage] = useState<string | null>(null);
  const [status, setStatus] = useState("draft");
  const [playerDetails, setPlayerDetails] = useState<Record<number, PlayerDetail>>({});
  // Keep the latest player metadata available to the long-lived auction WebSocket handler.
  const playerDetailsRef = useRef(playerDetails);
  playerDetailsRef.current = playerDetails;
  const [teamRosters, setTeamRosters] = useState<
    Record<number, { player_id: number; sold_price: number }[]>
  >({});

  const [selectedTeamId, setSelectedTeamId] = useState<number | null>(null);
  const [teamStripPage, setTeamStripPage] = useState(0);
  const [teamStripProgress, setTeamStripProgress] = useState(0);
  const teamStripProgressRef = useRef(0);
  const teamStripManualPauseUntil = useRef(0);
  const teamStripBidHoldUntil = useRef(0);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rtmRaiseAmount, setRtmRaiseAmount] = useState("");
  const [autoAdvance, setAutoAdvance] = useState(true);
  const advancingRef = useRef(false);
  const [justSoldPlayerId, setJustSoldPlayerId] = useState<number | null>(null);
  const [saleCelebration, setSaleCelebration] = useState<{
    playerName: string;
    playerPhoto?: string | null;
    playerCategory?: string | null;
    playerAge?: number | string | null;
    battingRating?: number | string | null;
    bowlingRating?: number | string | null;
    fieldingRating?: number | string | null;
    teamName: string;
    teamLogo?: string | null;
    soldPrice: number;
  } | null>(null);
  const [showEditBidModal, setShowEditBidModal] = useState(false);
  const [editBidInput, setEditBidInput] = useState("");
  const [activeDrawerTab, setActiveDrawerTab] = useState<
    "none" | "sold" | "unsold" | "available" | "teams" | "squads"
  >("none");
  const [selectedCategoryFilter, setSelectedCategoryFilter] = useState<number | "all">("all");
  const [selectedSquadTeamId, setSelectedSquadTeamId] = useState<number | null>(null);
  const [busyAction, setBusyAction] = useState(false);

  const syncState = useCallback(async () => {
    const [{ data }, teamRes] = await Promise.all([
      api.get(`/auction/events/${eid}/state`),
      api.get(`/auction/events/${eid}/teams`).catch(() => ({ data: [] })),
    ]);

    useAuctionStore.getState().setFullState(data);

    setStatus(data.status);
    setAutoAdvance(Boolean(data.auto_advance ?? true));
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
    async (opts: { silent?: boolean } = {}) => {
      if (advancingRef.current) return;
      advancingRef.current = true;
      if (!opts.silent) setBusyAction(true);
      try {
        await postIdempotent(`/auction/events/${eid}/next-player`, {
          auction_player_id: null,
        });
        setSelectedTeamId(null);
        setJustSoldPlayerId(null);
        // The player_up WebSocket message is emitted before this request
        // completes. Avoid a second full-roster fetch on the hot path.
        if (useAuctionStore.getState().connection !== "live") {
          await syncState();
        }
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

  const handleToggleAutoAdvance = async (enabled: boolean) => {
    const previous = autoAdvance;
    setAutoAdvance(enabled);
    try {
      await api.patch(`/auction/events/${eid}/auto-advance`, { auto_advance: enabled });
    } catch (error) {
      setAutoAdvance(previous);
      setActionError(describeError(error).message || "Could not update progression mode");
    }
  };


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
            setSaleCelebration(null);
            useAuctionStore.getState().setFullState(msg);
            setStatus(msg.status);
            setAutoAdvance(Boolean(msg.auto_advance ?? true));
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
            setSaleCelebration(null);
            if (msg.player_id) {
              setPlayerDetails((prev) => {
                const existing = prev[msg.player_id];
                return {
                  ...prev,
                  [msg.player_id]: {
                    ...existing,
                    player_id: msg.player_id,
                    name: msg.player_name || existing?.name || `Player #${msg.player_id}`,
                    // The player_up socket payload may omit profile data. Preserve
                    // the richer players-info response instead of erasing its photo.
                    profile_photo: msg.profile_photo ?? existing?.profile_photo,
                    batting_rating: msg.batting_rating ?? existing?.batting_rating,
                    bowling_rating: msg.bowling_rating ?? existing?.bowling_rating,
                    fielding_rating: msg.fielding_rating ?? existing?.fielding_rating,
                    age: msg.age ?? existing?.age,
                  },
                };
              });
            }
            useAuctionStore.getState().setActivePlayer(
              msg.auction_player_id,
              msg.base_price,
              msg.version,
              msg.deadline_epoch ?? null,
              msg.server_time
            );
            setSelectedTeamId(null);
            break;

          case "sale_reversed":
            setJustSoldPlayerId(null);
            setSaleCelebration(null);
            // The following auction_state snapshot refreshes the purse, roster,
            // and active player for every connected client.
            break;

          case "player_sold": {
            const currentAuctionState = useAuctionStore.getState();
            const soldPlayer = currentAuctionState.players.find(
              (player) => player.id === msg.auction_player_id
            );
            const winningTeam = currentAuctionState.teams.find(
              (team) => team.id === msg.sold_to_team_id
            );
            const soldPlayerInfo = soldPlayer
              ? playerDetailsRef.current[soldPlayer.player_id]
              : undefined;
            const soldPlayerCategory = currentAuctionState.categories.find(
              (category) => category.id === soldPlayer?.category_id
            );
            setSaleCelebration({
              playerName:
                msg.player_name ||
                soldPlayer?.name ||
                `Player #${soldPlayer?.player_id ?? msg.player_id ?? "—"}`,
              // Prefer the latest players-info record, then fall back to the photo
              // already carried by the active auction-player snapshot.
              playerPhoto:
                soldPlayerInfo?.profile_photo ||
                soldPlayer?.profile_photo ||
                null,
              playerCategory: soldPlayerCategory?.name || "EMERALD",
              playerAge: soldPlayerInfo?.age ?? null,
              battingRating: soldPlayerInfo?.batting_rating ?? null,
              bowlingRating: soldPlayerInfo?.bowling_rating ?? null,
              fieldingRating: soldPlayerInfo?.fielding_rating ?? null,
              teamName: msg.sold_to_team_name || winningTeam?.name || "Winning Team",
              teamLogo: winningTeam?.logo || null,
              soldPrice: Number(msg.sold_price || 0),
            });
            setJustSoldPlayerId(msg.auction_player_id);
            currentAuctionState.markPlayerSold(
              msg.auction_player_id,
              msg.sold_to_team_id ?? null,
              msg.sold_price
            );
            setSelectedTeamId(null);
            void api.get(`/auction/events/${eid}/teams`).then(({ data }) => {
              const rosters: Record<number, { player_id: number; sold_price: number }[]> = {};
              data.forEach((team: { id: number; players?: { player_id: number; sold_price: number }[] }) => {
                rosters[team.id] = team.players || [];
              });
              setTeamRosters(rosters);
            }).catch(() => {});
            break;
          }

          case "player_unsold":
            setJustSoldPlayerId(null);
            setSaleCelebration(null);
            useAuctionStore.getState().markPlayerUnsold(msg.auction_player_id);
            setSelectedTeamId(null);
            break;

          case "player_pulled":
            setJustSoldPlayerId(null);
            setSaleCelebration(null);
            useAuctionStore.getState().markPlayerUnsold(msg.auction_player_id);
            setSelectedTeamId(null);
            break;

          case "auction_paused":
            setStatus("paused");
            useAuctionStore.getState().setFullState({ status: "paused" });
            break;
          case "auction_resumed":
          case "auction_started":
            setStatus("active");
            // Keep the Zustand snapshot in sync with the page state.
            useAuctionStore.getState().setFullState({ status: "active" });
            break;
          case "auction_completed":
            setStatus("completed");
            useAuctionStore.getState().setFullState({ status: "completed" });
            break;
          case "auction_settings_updated":
            setAutoAdvance(Boolean(msg.auto_advance));
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

    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eid, hasAccess]);

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

  // Normalize IDs because API payloads may represent captain_id as a string
  // while auction player records use numeric player_id values.
  const captainIds = useMemo(
    () =>
      new Set(
        store.teams
          .map((team) => team.captain_id)
          .filter((id) => id !== null && id !== undefined)
          .map((id) => Number(id))
          .filter((id) => Number.isFinite(id))
      ),
    [store.teams]
  );

  const activeAP = store.players.find((p) => p.id === store.activePlayerId);
  // Captains are never auctionable. Do not let the just-sold display exception
  // reintroduce a captain into the bidding panel.
  const displayActiveAP =
    activeAP &&
    !captainIds.has(Number(activeAP.player_id)) &&
    (activeAP.status === "active" || justSoldPlayerId === activeAP.id)
      ? activeAP
      : null;

  const isPlayerSold =
    !!displayActiveAP &&
    (displayActiveAP.status === "sold" || justSoldPlayerId === displayActiveAP.id);

  // Keep every auction count aligned to the eligible auction pool. Assigned
  // captains are excluded from both the progress denominator and status totals.
  const auctionPoolPlayers = useMemo(
    () => store.players.filter((p) => !captainIds.has(Number(p.player_id))),
    [store.players, captainIds]
  );
  const pendingPlayers = useMemo(
    () => auctionPoolPlayers.filter((p) => p.status === "pending"),
    [auctionPoolPlayers]
  );
  const soldPlayers = useMemo(
    () => auctionPoolPlayers.filter((p) => p.status === "sold"),
    [auctionPoolPlayers]
  );
  const queuedPlayers = useMemo(
    () => auctionPoolPlayers.filter((p) => p.status === "queued"),
    [auctionPoolPlayers]
  );
  const soldPlayersAndQueue = useMemo(
    () => [...soldPlayers, ...queuedPlayers],
    [soldPlayers, queuedPlayers]
  );
  const unsoldPlayers = useMemo(
    () => auctionPoolPlayers.filter((p) => p.status === "unsold"),
    [auctionPoolPlayers]
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

  // Only players who can actually be auctioned may determine the minimum
  // reserve price. Categories used exclusively by pre-assigned captains must
  // never reserve purse money because captains are free and not auction lots.
  const minEventBasePrice = useMemo(
    () =>
      getMinimumAuctionBasePrice(
        store.players,
        captainIds,
        getEffectiveBasePrice
      ),
    [store.players, captainIds, getEffectiveBasePrice]
  );

  const getTeamMaxBid = useCallback(
    (team: {
      id: number;
      budget: number;
      spent: number;
      max_players: number;
      player_count: number;
      captain_id?: number | null;
    }) => {
      // A pre-assigned captain is a free squad assignment, not an auction
      // purchase. If legacy/team-roster data includes a captain row in spent,
      // add that amount back before calculating the team's available purse.
      const captainPrice =
        team.captain_id == null
          ? 0
          : teamRosters[team.id]?.find(
              (player) => player.player_id === team.captain_id
            )?.sold_price ?? 0;
      const remainingPurse = Math.max(
        0,
        team.budget - team.spent + captainPrice
      );
      const slotsLeft = Math.max(0, team.max_players - team.player_count);
      if (slotsLeft <= 0) return 0;

      const minSquad = store.rules?.team_min_players ?? 0;
      // The captain occupies a max-squad slot but is not an auction purchase.
      // Exclude the captain from the minimum number of paid players to acquire.
      const reserveSlots = getRemainingAuctionReserveSlots(
        minSquad,
        team.player_count,
        team.captain_id != null
      );
      const reserve = reserveSlots * minEventBasePrice;
      return Math.max(0, remainingPurse - reserve);
    },
    [minEventBasePrice, store.rules, teamRosters]
  );

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
      // The connected WebSocket already owns the current snapshot and receives
      // player_up / auction_started events. Avoid a duplicate full roster fetch.
      setStatus("active");
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
      if (useAuctionStore.getState().connection !== "live") {
        await syncState();
      }
    } catch (e: unknown) {
      alert(describeError(e).message || "Error selecting player");
    } finally {
      setBusyAction(false);
    }
  };

  const handleReverseSale = async (auctionPlayerId: number, playerName: string, teamName: string, salePrice: number) => {
    if (status !== "active" || busyAction) return;
    const queued = Boolean(displayActiveAP);
    const ok = confirm(
      `Reverse the sale of ${playerName} to ${teamName} for ₹${formatMoney(salePrice)}?\n\nThe amount will be refunded to ${teamName} and the player removed from that squad. ${queued ? "The player will be queued and automatically appear as soon as the current player's auction is finalised." : "The player will immediately return to the auction block at the applicable base price."}`
    );
    if (!ok) return;
    setBusyAction(true);
    setActionError(null);
    try {
      await api.post(`/auction/events/${eid}/players/${auctionPlayerId}/reverse-sale`);
      setSelectedTeamId(null);
      setJustSoldPlayerId(null);
      setActiveDrawerTab("none");
      if (useAuctionStore.getState().connection !== "live") await syncState();
    } catch (error: unknown) {
      const info = describeError(error);
      setActionError(info.message || "Could not reverse the sale");
      await syncState().catch(() => {});
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

  const lotTotal = auctionPoolPlayers.length;
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
      await postIdempotent(`/auction/events/${eid}/hammer`);
      setSelectedTeamId(null);
      setJustSoldPlayerId(null);
      useAuctionStore.getState().markPlayerUnsold(displayActiveAP.id);
    } catch (e: unknown) {
      alert(describeError(e).message || "Error marking no bid");
    } finally {
      setBusyAction(false);
    }
  };

  const handleSubmit = async () => {
    if (!displayActiveAP?.current_bid_team_id) return;
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
    if (unsoldPlayers.length > 0) {
      alert("Unsold players must be re-auctioned before finishing.");
      return;
    }
    if (!confirm("Complete and lock this auction permanently?")) return;
    await api.post(`/auction/events/${eid}/finish`);
    await syncState();
  };

  const activeInfo = displayActiveAP
    ? playerDetails[displayActiveAP.player_id] ?? {
        player_id: displayActiveAP.player_id,
        name: displayActiveAP.name ?? `Player #${displayActiveAP.player_id}`,
        profile_photo: displayActiveAP.profile_photo ?? undefined,
      }
    : undefined;
  const activeCategory = displayActiveAP
    ? getCategoryObj(displayActiveAP.category_id)
    : undefined;

  const nameRef = useFitText<HTMLHeadingElement>(16, 36, [
    activeInfo?.name,
    displayActiveAP?.player_id,
  ]);

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

  const teamStripPageCount = Math.max(1, Math.ceil(store.teams.length / 6));
  const stripTeams = store.teams.length <= 6
    ? store.teams
    : store.teams.slice(teamStripPage * 6, teamStripPage * 6 + 6);

  useEffect(() => {
    if (store.teams.length <= 6 || status !== "active") {
      if (store.teams.length <= 6) setTeamStripPage(0);
      teamStripProgressRef.current = 0;
      setTeamStripProgress(0);
      return;
    }

    const rotationMs = 7000;
    const tickMs = 250;
    const timer = window.setInterval(() => {
      const now = Date.now();
      const pausedUntil = Math.max(
        teamStripManualPauseUntil.current,
        teamStripBidHoldUntil.current
      );
      if (now < pausedUntil) {
        teamStripProgressRef.current = 0;
        setTeamStripProgress(0);
        return;
      }

      const next = teamStripProgressRef.current + (tickMs / rotationMs) * 100;
      if (next >= 100) {
        teamStripProgressRef.current = 0;
        setTeamStripProgress(0);
        setTeamStripPage((page) => (page + 1) % teamStripPageCount);
      } else {
        teamStripProgressRef.current = next;
        setTeamStripProgress(next);
      }
    }, tickMs);

    return () => window.clearInterval(timer);
  }, [store.teams.length, teamStripPageCount, status]);

  useEffect(() => {
    if (!leadingTeam || store.teams.length <= 6) return;
    const teamIndex = store.teams.findIndex((team) => team.id === leadingTeam.id);
    if (teamIndex < 0) return;
    setTeamStripPage(Math.floor(teamIndex / 6));
    teamStripProgressRef.current = 0;
    setTeamStripProgress(0);
    teamStripBidHoldUntil.current = Date.now() + 15000;
  }, [leadingTeam?.id, displayedCurrentBid, store.teams]);

  const navigateTeamStrip = (direction: -1 | 1) => {
    if (teamStripPageCount <= 1) return;
    setTeamStripPage((page) => (page + direction + teamStripPageCount) % teamStripPageCount);
    teamStripProgressRef.current = 0;
    setTeamStripProgress(0);
    teamStripManualPauseUntil.current = Date.now() + 15000;
  };

  if (checkingAccess) {
    return (
      <div className="h-[100dvh] w-full bg-[#000000] flex items-center justify-center text-amber-300 font-bold tracking-wider uppercase text-sm">
        Loading Auctioneer Console...
      </div>
    );
  }

  if (!hasAccess) return null;

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
                {soldPlayers.length} / {lotTotal || "XX"}
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
                {unsoldPlayers.length}
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
                  className="flex flex-col items-center text-white hover:text-amber-300 disabled:opacity-100 transition"
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
        {/* Diagonal crimson divider line */}
        <div
          aria-hidden="true"
          className="pointer-events-none hidden lg:block absolute top-0 bottom-0 left-[44%] w-1 bg-[#9e0b05]/80 -skew-x-[10deg]"
        />

        <div className="relative z-10 mx-auto h-full w-full max-w-[1920px] grid grid-cols-1 lg:grid-cols-12 gap-2.5 px-5 py-3">
          {/* LEFT ZONE: Player Showcase Card (Photo Placeholder) */}
          <div className="lg:col-span-6 min-h-0 flex flex-col justify-center">
            {displayActiveAP ? (
              <div className="bg-[#f4f4f2] text-[#111111] shadow-2xl overflow-hidden flex flex-col max-h-full w-full max-w-[460px] mx-auto lg:mx-0">
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
            ) : saleCelebration ? (
              <div className="bg-[#f4f4f2] text-[#111111] shadow-2xl overflow-hidden flex flex-col max-h-full w-full max-w-[460px] mx-auto lg:mx-0">
                <div className="relative flex-1 min-h-0 bg-[#dcdcd8] overflow-hidden">
                  {saleCelebration.playerPhoto && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={saleCelebration.playerPhoto} alt={saleCelebration.playerName} className="w-full h-full object-cover object-top" />
                  )}
                </div>
                <div className="px-3.5 pt-2 pb-1.5 flex items-end gap-3 border-b border-neutral-300">
                  <div className="shrink-0 flex flex-col items-center gap-0.5 pb-0.5">
                    <span className="bg-[#f59e0b] text-white text-[9px] font-black uppercase tracking-wider px-2 py-0.5 rounded-full">{saleCelebration.playerCategory || "EMERALD"}</span>
                    <span className="text-[10px] font-bold text-neutral-600">SOLD</span>
                  </div>
                  <div className="min-w-0 flex-1 h-10 flex items-center">
                    <h2 className="w-full font-black uppercase tracking-tight text-[#111111] leading-none truncate">{saleCelebration.playerName}</h2>
                  </div>
                </div>
                <div className="grid grid-cols-4 px-3.5 py-2 text-center bg-[#f4f4f2]">
                  <div><div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">AGE</div><div className="text-sm font-black text-[#111111] tabular-nums">{saleCelebration.playerAge ?? "—"}</div></div>
                  <div><div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">BAT</div><div className="text-sm font-black text-[#111111] tabular-nums">{saleCelebration.battingRating ?? "—"}</div></div>
                  <div><div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">BALL</div><div className="text-sm font-black text-[#111111] tabular-nums">{saleCelebration.bowlingRating ?? "—"}</div></div>
                  <div><div className="text-[9px] font-bold uppercase tracking-wider text-neutral-600">FIELD</div><div className="text-sm font-black text-[#111111] tabular-nums">{saleCelebration.fieldingRating ?? "—"}</div></div>
                </div>
              </div>
            ) : (
              <div className="bg-black/45 border border-white/20 p-6 text-center max-w-[460px] w-full mx-auto lg:mx-0">
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
                className="bg-black/90 hover:bg-black disabled:opacity-100 border border-white/25 px-2 py-1.5 text-center transition flex flex-col items-center justify-center"
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
                className="bg-black/90 hover:bg-black disabled:opacity-100 border border-white/25 px-2 py-1.5 text-center transition flex flex-col items-center justify-center"
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
                className="bg-black/90 hover:bg-black disabled:opacity-100 border border-white/25 px-2 py-1.5 text-center transition flex flex-col items-center justify-center"
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
                disabled={
                  status !== "active" ||
                  !displayActiveAP ||
                  !displayActiveAP.current_bid_team_id ||
                  busyAction
                }
                className="w-full bg-[#e61208] hover:bg-[#ff190d] disabled:opacity-100 text-white font-black uppercase tracking-wider text-xs sm:text-sm py-2 shadow-lg transition"
              >
                SUBMIT
              </button>

              <div className="flex items-center justify-between mt-1">
                <label className="inline-flex items-center gap-1.5 text-[10px] text-white/80 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoAdvance}
                    onChange={(e) => void handleToggleAutoAdvance(e.target.checked)}
                    className="accent-amber-300 w-3 h-3"
                  />
                  <span>Auto-advance</span>
                </label>

                <div className="flex items-center gap-3">
                  {unsoldPlayers.length > 0 && pendingPlayers.length === 0 && status === "active" && (
                    <button type="button" onClick={reAuctionAllUnsold} disabled={busyAction || !!displayActiveAP} className="text-[11px] text-amber-300 underline hover:text-amber-200 disabled:opacity-100">re-auction unsold</button>
                  )}
                  <button type="button" onClick={() => { setSelectedSquadTeamId(store.teams[0]?.id ?? null); setActiveDrawerTab("squads"); }} className="text-[11px] text-white underline hover:text-amber-200 font-medium">Squad Overview</button>
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

            {saleCelebration ? (
              <div className="relative isolate flex flex-1 min-h-0 w-full flex-col items-center justify-center overflow-hidden py-2 text-center">
                <style>{`
                  @keyframes sold-banner-pop { 0% { opacity: 0; transform: translateY(18px) scale(.72) rotate(-3deg); } 70% { opacity: 1; transform: translateY(-3px) scale(1.04) rotate(1deg); } 100% { opacity: 1; transform: translateY(0) scale(1) rotate(0); } }
                  @keyframes sold-panel-rise { 0% { opacity: 0; transform: translateY(24px); } 100% { opacity: 1; transform: translateY(0); } }
                  @keyframes sold-firework-burst { 0% { opacity: 0; transform: scale(.25) rotate(-18deg); } 35% { opacity: 1; transform: scale(1.15) rotate(8deg); } 100% { opacity: 0; transform: scale(1.65) rotate(24deg); } }
                  @keyframes sold-confetti-fall { 0% { opacity: 0; transform: translateY(-24px) rotate(0); } 18% { opacity: 1; } 100% { opacity: 0; transform: translateY(430px) rotate(560deg); } }
                  .sold-banner-pop { animation: sold-banner-pop .7s cubic-bezier(.2,.9,.25,1.2) both; }
                  .sold-panel-rise { animation: sold-panel-rise .55s ease-out both; }
                  .sold-firework-burst { animation: sold-firework-burst 1.8s ease-out infinite; }
                  .sold-confetti-fall { animation: sold-confetti-fall 3.2s linear infinite; }
                  @media (prefers-reduced-motion: reduce) { .sold-banner-pop, .sold-panel-rise, .sold-firework-burst, .sold-confetti-fall { animation: none !important; } }
                `}</style>
                <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
                  <span className="sold-firework-burst absolute left-[3%] top-[8%] text-4xl">🎆</span>
                  <span className="sold-firework-burst absolute right-[4%] top-[7%] text-5xl" style={{ animationDelay: ".45s" }}>🎇</span>
                  <span className="sold-firework-burst absolute left-[18%] bottom-[14%] text-3xl" style={{ animationDelay: ".9s" }}>🎆</span>
                  <span className="sold-firework-burst absolute right-[17%] bottom-[12%] text-4xl" style={{ animationDelay: ".2s" }}>🎇</span>
                  <span className="sold-confetti-fall absolute left-[13%] top-0 text-xl" style={{ animationDelay: ".1s" }}>🎊</span>
                  <span className="sold-confetti-fall absolute left-[39%] top-0 text-xl" style={{ animationDelay: ".75s" }}>✨</span>
                  <span className="sold-confetti-fall absolute right-[34%] top-0 text-xl" style={{ animationDelay: "1.15s" }}>🎉</span>
                  <span className="sold-confetti-fall absolute right-[9%] top-0 text-xl" style={{ animationDelay: ".5s" }}>✨</span>
                </div>
                <div className="relative z-10 flex w-full max-w-[620px] flex-col items-center">
                  <div className="sold-banner-pop mb-2 w-full rounded-t-[48%] rounded-b-xl border-y-4 border-amber-200 bg-gradient-to-b from-yellow-300 via-amber-400 to-yellow-600 px-4 py-2 shadow-[0_8px_30px_rgba(255,190,0,0.5)]">
                    <p className="text-4xl sm:text-6xl font-black italic uppercase tracking-wider text-red-700 drop-shadow-[0_3px_0_rgba(255,255,255,0.55)]">SOLD!</p>
                  </div>
                  <div className="sold-panel-rise grid w-full grid-cols-[1fr_auto_1.35fr] items-center gap-3 rounded-xl border border-amber-200/80 bg-gradient-to-r from-[#a66d00] via-[#d6a500] to-[#a66d00] px-4 py-4 text-left shadow-[0_0_30px_rgba(255,193,7,0.45)]">
                    <div className="min-w-0">
                      <p className="text-[10px] sm:text-xs font-bold uppercase tracking-wider text-white/90">BID FOR</p>
                      <p className="mt-1 flex flex-wrap items-baseline gap-x-2 text-white">
                        <span className="text-4xl sm:text-5xl font-black leading-none tabular-nums">{formatAuctionPrice(saleCelebration.soldPrice).value}</span>
                        <span className="text-sm sm:text-lg font-extrabold uppercase">{formatAuctionPrice(saleCelebration.soldPrice).unit}</span>
                      </p>
                    </div>
                    <div aria-hidden="true" className="h-16 w-px bg-white/75" />
                    <div className="flex min-w-0 items-center gap-3">
                      <div className="h-16 w-16 shrink-0 overflow-hidden rounded-lg bg-black/20 p-1">
                        {saleCelebration.teamLogo ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={resolveMediaUrl(saleCelebration.teamLogo)} alt="" className="h-full w-full object-contain" />
                        ) : (
                          <span className="flex h-full items-center justify-center text-4xl" aria-hidden="true">🏆</span>
                        )}
                      </div>
                      <div className="min-w-0">
                        <p className="text-[10px] sm:text-xs font-bold uppercase tracking-wider text-white/90">BIDDING TEAM</p>
                        <p className="mt-1 text-base sm:text-xl font-black uppercase leading-tight text-white break-words">{saleCelebration.teamName}</p>
                      </div>
                    </div>
                  </div>
                  <div className="sold-panel-rise mt-5 flex items-center justify-center gap-3 text-center" style={{ animationDelay: ".15s" }}>
                    <span className="text-4xl sm:text-5xl" aria-hidden="true">🏆</span>
                    <p className="text-xl sm:text-2xl font-black text-white drop-shadow-lg">Congratulations on the signing!</p>
                  </div>
                  <div className="sold-panel-rise mt-4 rounded-full border border-amber-300/60 bg-black/45 px-7 py-2 text-[10px] sm:text-xs font-black uppercase tracking-[0.25em] text-white shadow-lg" style={{ animationDelay: ".25s" }}>
                    NEXT PLAYER COMING UP...
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex flex-1 min-h-0 flex-col justify-center py-2">
                {/* Two-tier banner: Gold BID FOR / SOLD FOR + Black TEAM NAME / BIDDING TEAM / WINNING TEAM */}
                <div className="w-full shadow-2xl overflow-hidden">
                  {/* Upper tier: Mustard/Gold (#B8860B) */}
                  <div className="relative bg-[#b8860b] px-4 py-2.5 text-white">
                    <div className="text-[9px] font-bold uppercase tracking-wider text-white/90">
                      {isPlayerSold ? "SOLD FOR" : "BID FOR"}
                    </div>

                    <div className="mt-1 flex items-baseline gap-2">
                      {(() => {
                        const formattedPrice = displayActiveAP
                          ? formatAuctionPrice(displayedCurrentBid)
                          : { value: "XX", unit: "" };
                        return (
                          <>
                            <span className="text-3xl sm:text-5xl font-black tracking-tight tabular-nums leading-none">
                              {formattedPrice.value}
                            </span>
                            {formattedPrice.unit && (
                              <span className="text-sm sm:text-lg font-extrabold uppercase tracking-wide">
                                {formattedPrice.unit}
                              </span>
                            )}
                          </>
                        );
                      })()}
                    </div>

                    <button
                      type="button"
                      onClick={handleOpenEditBid}
                      disabled={!displayActiveAP}
                      title="Edit bid amount manually"
                      className="absolute top-2 right-2 w-7 h-7 flex items-center justify-center text-xs text-white"
                    >
                      <span className="inline-block -scale-x-100">✎</span>
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
            )}
          </div>
        </div>
      </main>

      {/* ── ADAPTIVE, AUTO-ROTATING TEAM STRIP ── */}
      <footer className="bg-[#000000] border-t-2 border-[#8b0000]">
        {store.teams.length > 6 && (
          <div className="mx-auto flex max-w-[1920px] items-center justify-between gap-3 px-3 pt-1.5">
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-bold uppercase tracking-wider text-white/65">Teams</span>
              <span className="text-[10px] font-extrabold tabular-nums text-amber-300">
                {teamStripPage * 6 + 1}–{Math.min((teamStripPage + 1) * 6, store.teams.length)} of {store.teams.length}
              </span>
              <div className="ml-1 flex items-center gap-1" aria-label={`Page ${teamStripPage + 1} of ${teamStripPageCount}`}>
                {Array.from({ length: teamStripPageCount }, (_, index) => (
                  <span key={index} className={`h-1.5 w-1.5 rounded-full ${index === teamStripPage ? "bg-amber-400" : "bg-white/25"}`} />
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden sm:inline text-[9px] text-white/45">AUTO ROTATES</span>
              <button type="button" onClick={() => navigateTeamStrip(-1)} aria-label="Previous teams" className="flex h-6 w-7 items-center justify-center rounded bg-white/10 text-sm font-bold text-white hover:bg-white/20">‹</button>
              <button type="button" onClick={() => navigateTeamStrip(1)} aria-label="Next teams" className="flex h-6 w-7 items-center justify-center rounded bg-white/10 text-sm font-bold text-white hover:bg-white/20">›</button>
            </div>
          </div>
        )}
        <div
          className={`mx-auto grid w-full max-w-[1920px] divide-y sm:divide-y-0 lg:divide-x divide-[#333333] grid-cols-2 sm:grid-cols-3 ${
            store.teams.length <= 1 ? "lg:grid-cols-1" :
            store.teams.length === 2 ? "lg:grid-cols-2" :
            store.teams.length === 3 ? "lg:grid-cols-3" :
            store.teams.length === 4 ? "lg:grid-cols-4" :
            store.teams.length === 5 ? "lg:grid-cols-5" : "lg:grid-cols-6"
          }`}
        >
          <div className={`contents ${store.teams.length <= 6 ? (store.teams.length === 4 ? "lg:[&>*]:col-span-1" : "") : ""}`}>
            {stripTeams.map((team) => {
              const remainingPurse = Math.max(0, team.budget - team.spent);
              const maxBid = getTeamMaxBid(team);
              const pursePrice = formatAuctionPrice(remainingPurse);
              const maxBidPrice = formatAuctionPrice(maxBid);
              const isLeading = !!displayActiveAP && displayActiveAP.current_bid_team_id === team.id;
              const isSelected = selectedTeamId === team.id;
              const cannotAfford = !!displayActiveAP && maxBid < teamBidAmount;
              const isFull = team.player_count >= team.max_players;
              const disabled =
                status !== "active" || !displayActiveAP || isLeading ||
                cannotAfford || isFull || busyAction;

              return (
                <div
                  key={team.id}
                  onClick={() => {
                    if (!disabled) placeBidForTeam(team.id, teamBidAmount);
                  }}
                  title={disabled ? team.name : `Click to bid ₹${formatMoney(nextBidAmount)} for ${team.name}`}
                  className={`px-2.5 py-2 flex flex-col justify-between transition lg:col-span-1 ${isLeading ? "bg-amber-950/50 ring-1 ring-inset ring-amber-400" : isSelected ? "bg-neutral-900 ring-1 ring-inset ring-amber-300" : disabled ? "opacity-75 cursor-default" : "cursor-pointer hover:bg-neutral-900"}`}
                >
                  <div className="flex items-start gap-2 min-w-0">
                    <div className="w-8 h-8 shrink-0 flex items-center justify-center overflow-hidden">
                      {team.logo && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={team.logo} alt={team.name} className="w-full h-full object-cover" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-bold text-white truncate leading-tight">{team.name}</div>
                      <div className="text-[10px] text-white/75 tabular-nums leading-tight mt-0.5">{team.player_count}/{team.max_players} Players</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-1.5 mt-2 pt-1">
                    <div>
                      <div className="text-[9px] text-white/65 leading-none">Purse</div>
                      <div className="text-sm sm:text-base font-extrabold text-white tabular-nums truncate mt-0.5">₹{pursePrice.value} <span className="text-[9px] sm:text-[10px] font-bold text-white/75">{pursePrice.unit}</span></div>
                    </div>
                    <div>
                      <div className="text-[9px] text-white/65 leading-none">Max Bid</div>
                      <div className="text-sm sm:text-base font-extrabold text-white tabular-nums truncate mt-0.5">₹{maxBidPrice.value} <span className="text-[9px] sm:text-[10px] font-bold text-white/75">{maxBidPrice.unit}</span></div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        {store.teams.length > 6 && (
          <div className="h-[2px] w-full bg-white/10">
            <div className="h-full bg-amber-400/80 transition-[width] duration-100" style={{ width: `${teamStripProgress}%` }} />
          </div>
        )}
      </footer>

      {/* ── DRAWER FOR SOLD / UNSOLD / AVAILABLE / TEAMS ── */}
      {activeDrawerTab !== "none" && (
        <div className="fixed inset-0 z-40 bg-black/75 flex items-end sm:items-center justify-center p-4">
          <div className={`relative bg-slate-900 border border-slate-700 rounded-2xl w-full ${activeDrawerTab === "squads" ? "max-w-[96vw] h-[94vh] max-h-[1100px]" : "max-w-2xl max-h-[80vh]"} flex flex-col overflow-hidden shadow-2xl`}>
            {activeDrawerTab === "squads" ? (
              <button
                onClick={() => setActiveDrawerTab("none")}
                aria-label="Close Squad Overview"
                className="absolute right-3 top-3 z-30 flex h-8 w-8 items-center justify-center rounded-full bg-slate-950/80 text-slate-300 text-lg font-bold shadow-lg ring-1 ring-white/15 hover:bg-slate-800 hover:text-white"
              >
                ✕
              </button>
            ) : (
              <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
                <h3 className="font-extrabold text-base uppercase tracking-wide text-amber-300">
                  {activeDrawerTab === "sold" &&
                    `Sold Players (${soldPlayers.length}) · Queued (${queuedPlayers.length})`}
                  {activeDrawerTab === "unsold" &&
                    `Unsold Players (${unsoldPlayers.length})`}
                  {activeDrawerTab === "available" &&
                    `Pick Manually (${filteredPending.length + unsoldPlayers.length})`}
                  {activeDrawerTab === "teams" && `Teams Summary (${store.teams.length})`}
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
                    unsoldPlayers.length === 0 &&
                    !auctionPoolPlayers.some((player) => player.status === "queued") &&
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
                    aria-label="Close"
                    className="text-slate-400 hover:text-white text-lg font-bold px-2"
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}

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

            <div className={`${activeDrawerTab === "squads" ? "p-2 overflow-hidden" : "p-4 overflow-y-auto"} space-y-2 flex-1 min-h-0`}>
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
                          className="bg-amber-400 hover:bg-amber-300 disabled:opacity-100 text-slate-950 font-extrabold text-xs px-3 py-1.5 rounded-lg"
                        >
                          Bring to Block
                        </button>
                      </div>
                    );
                  })
                ))}

              {activeDrawerTab === "available" && (
                <section className="mt-5 border-t border-slate-700 pt-4">
                  <div className="mb-3 flex items-center justify-between">
                    <h4 className="text-sm font-extrabold uppercase tracking-wide text-red-300">
                      Unsold ({unsoldPlayers.length})
                    </h4>
                    {unsoldPlayers.length > 0 && pendingPlayers.length > 0 && (
                      <span className="text-[10px] font-semibold text-slate-400">
                        Available after the first round
                      </span>
                    )}
                  </div>
                  {unsoldPlayers.length === 0 ? (
                    <p className="py-3 text-center text-xs text-slate-500">
                      No unsold players.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {unsoldPlayers.map((p) => {
                        const info = playerDetails[p.player_id];
                        const effBase = getEffectiveBasePrice(p);
                        const canReAuction =
                          pendingPlayers.length === 0 &&
                          status === "active" &&
                          !displayActiveAP &&
                          !busyAction;
                        return (
                          <div
                            key={p.id}
                            className="flex items-center justify-between rounded-xl border border-red-500/30 bg-slate-800/80 px-3 py-2.5"
                          >
                            <div className="min-w-0">
                              <p className="font-bold text-sm text-white">
                                #{p.player_id} {info?.name || `Player #${p.player_id}`}
                              </p>
                              <p className="text-xs text-slate-400">
                                Base: ₹{formatMoney(effBase)}
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() => handlePickPlayer(p.id)}
                              disabled={!canReAuction}
                              title={
                                pendingPlayers.length > 0
                                  ? "Unsold players become available after all first-round players have been auctioned"
                                  : undefined
                              }
                              className="shrink-0 rounded-lg bg-amber-400 px-3 py-1.5 text-xs font-extrabold text-slate-950 hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              Re-Auction
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </section>
              )}

              {activeDrawerTab === "sold" &&
                (soldPlayersAndQueue.length === 0 ? (
                  <p className="text-center text-slate-500 text-sm py-8">
                    No players sold yet.
                  </p>
                ) : (
                  soldPlayersAndQueue.map((p) => {
                    const info = playerDetails[p.player_id];
                    const buyer = store.teams.find(
                      (t) => t.id === p.current_bid_team_id
                    );
                    return (
                      <div
                        key={p.id}
                        className={`flex items-center justify-between bg-slate-800/80 border rounded-xl px-3 py-2.5 ${p.status === "queued" ? "border-amber-400/40" : "border-emerald-500/30"}`}
                      >
                        <div>
                          <p className="font-bold text-sm text-white">
                            {info?.name || `Player #${p.player_id}`}
                          </p>
                          <p className={`text-xs ${p.status === "queued" ? "text-amber-300" : "text-emerald-400"}`}>
                            {p.status === "queued" ? "Queued for re-auction" : `Sold to ${buyer?.name || "Team"}`}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-3">
                          {p.status === "sold" && <span className="font-black text-sm text-emerald-300">₹{formatMoney(p.current_bid)}</span>}
                          {p.status === "queued" ? (
                            <span className="rounded-lg bg-amber-400/15 px-3 py-1.5 text-xs font-extrabold text-amber-300">Queued</span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleReverseSale(
                                p.id,
                                info?.name || `Player #${p.player_id}`,
                                buyer?.name || "the original team",
                                p.current_bid
                              )}
                              disabled={status !== "active" || busyAction}
                              title={status !== "active" ? "Sale correction is available while the auction is active" : displayActiveAP ? "Queue this player to appear after the current lot is finalised" : "Refund the team and return this player to the auction block"}
                              className="rounded-lg bg-red-600/90 px-3 py-1.5 text-xs font-extrabold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              Re-auction
                            </button>
                          )}
                        </div>
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
                        <button
                          onClick={() => handlePickPlayer(p.id)}
                          disabled={
                            status !== "active" ||
                            !!displayActiveAP ||
                            pendingPlayers.length > 0 ||
                            busyAction
                          }
                          title={
                            pendingPlayers.length > 0
                              ? "Unsold players become available after all first-round players have been auctioned"
                              : undefined
                          }
                          className="rounded-lg bg-amber-400 px-3 py-1.5 text-xs font-extrabold text-slate-950 hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          Re-Auction
                        </button>
                      </div>
                    );
                  })
                ))}

              {activeDrawerTab === "squads" && (
                <div className="relative isolate flex min-h-0 h-full flex-1 flex-row gap-3">
                  <nav className="flex w-40 shrink-0 flex-col gap-2 overflow-y-auto border-r border-slate-700 pr-3" aria-label="Select team squad">
                    {store.teams.map((team) => (
                      <button
                        key={team.id}
                        onClick={() => setSelectedSquadTeamId(team.id)}
                        className={`w-full shrink-0 rounded-lg px-3 py-3 text-left text-sm font-extrabold ${selectedSquadTeamId === team.id ? "bg-amber-400 text-slate-950" : "bg-slate-800 text-slate-300 hover:bg-slate-700"}`}
                      >
                        {team.name}
                      </button>
                    ))}
                  </nav>
                  {(() => {
                    const team = store.teams.find((item) => item.id === selectedSquadTeamId) || store.teams[0];
                    if (!team) return <p className="text-center text-slate-500 py-8">No teams configured.</p>;
                    const roster = teamRosters[team.id] || [];
                    const captainId = team.captain_id;
                    const captainPurchase = captainId ? roster.find((p) => p.player_id === captainId) : undefined;
                    const players = roster.filter((p) => p.player_id !== captainId);
                    const renderCard = (playerId: number, soldPrice?: number, captain = false) => {
                      const info = playerDetails[playerId];
                      const name = info?.name || (captain && team.captain_name) || `Player #${playerId}`;
                      return (
                        <div key={playerId} className={`group relative aspect-[6/7] w-full max-w-[202px] min-h-0 overflow-hidden rounded-xl border text-center shadow-lg transition hover:-translate-y-1 ${captain ? "border-amber-400/70 ring-1 ring-amber-400/20" : "border-slate-700"}`}>
                          {info?.profile_photo ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={info.profile_photo} alt={name} className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105" />
                          ) : (
                            <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-slate-600 to-slate-900"><span className="text-7xl font-black text-slate-300">{name.charAt(0).toUpperCase()}</span></div>
                          )}
                          <div className="absolute inset-0 bg-gradient-to-b from-black/55 via-transparent to-black/90" />
                          {captain && <span aria-label="Captain" title="Captain" className="absolute left-2 top-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-amber-300/70 bg-black/65 p-0 text-sm font-black leading-none text-amber-300">★</span>}
                          <div className="absolute inset-x-0 bottom-0 z-10 px-2 pb-2 pt-10">
                            <p className="w-full truncate text-sm sm:text-base font-extrabold text-white drop-shadow-lg">{name}</p>
                            {soldPrice !== undefined && (
                              <div className="mt-1 flex flex-col items-center justify-center">
                                <span className="text-[9px] font-bold uppercase tracking-wider text-white/75">Winning Bid</span>
                                <strong className="mt-0.5 text-lg sm:text-xl font-black tabular-nums text-amber-300 drop-shadow-lg">₹{formatMoney(soldPrice)}</strong>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    };
                    return (
                      <div className="relative isolate flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                        {team.logo && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={resolveMediaUrl(team.logo)}
                            alt=""
                            aria-hidden="true"
                            className="pointer-events-none absolute left-1/2 top-1/2 z-20 h-auto w-[min(48vw,560px)] max-h-[70%] -translate-x-1/2 -translate-y-1/2 object-contain opacity-[0.20]"
                          />
                        )}
                        <div className="relative z-30 mb-2 shrink-0 pr-10 text-xs text-slate-300">
                          <strong className="text-lg text-white">Squad Overview · {team.name}</strong>
                        </div>
                        <div className="relative z-10 min-h-0 flex-1 overflow-hidden">
                          {captainId || roster.length > 0 ? (
                            <div className="grid grid-cols-2 justify-items-center content-start gap-2 sm:grid-cols-4 lg:grid-cols-6">
                              {captainId && renderCard(captainId, captainPurchase?.sold_price, true)}
                              {players.map((p) => renderCard(p.player_id, p.sold_price))}
                            </div>
                          ) : <p className="py-12 text-center text-sm text-slate-500">No captain or players assigned yet.</p>}
                        </div>
                        <div className="relative z-30 mt-2 shrink-0 border-t border-slate-700 pt-2 text-right text-xs font-semibold text-slate-300 tabular-nums">
                          {team.player_count}/{team.max_players} players · Purse ₹{formatMoney(team.budget - team.spent)}
                        </div>
                      </div>
                    );
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

    </div>
  );
}
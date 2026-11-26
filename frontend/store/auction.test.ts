/**
 * Regression tests for the infinite render loop (React error #185).
 *
 * New in 2.1.3 (2026-11-21).
 *
 * The auctioneer console white-screened with "Application error: a client-side
 * exception has occurred", preceded by dozens of
 * "WebSocket is closed before the connection is established".
 *
 * Cause: `const store = useAuctionStore()` subscribes to the WHOLE store, so
 * that reference changes on every state update. `store` was listed in the
 * WebSocket effect's dependencies, and the effect's own onStateChange handler
 * called setConnection(). So: connect -> setConnection -> state change -> new
 * store reference -> effect re-runs -> socket torn down and rebuilt ->
 * setConnection -> ... forever.
 *
 * The dependency lists are fixed, but these tests pin the store-level guard
 * that stops the feedback at its source, so a future dependency mistake cannot
 * bring the loop back.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { useAuctionStore } from "./auction";

const initial = useAuctionStore.getState();

beforeEach(() => {
  useAuctionStore.setState(initial, true);
});

describe("setConnection", () => {
  it("does not notify subscribers when the value is unchanged", () => {
    const { setConnection } = useAuctionStore.getState();
    setConnection("live");

    let notifications = 0;
    const unsubscribe = useAuctionStore.subscribe(() => {
      notifications += 1;
    });

    // This is the loop: the handler fires on every reconnect attempt, and each
    // notification produced a new store reference for the effect to react to.
    setConnection("live");
    setConnection("live");
    setConnection("live");

    unsubscribe();
    expect(notifications).toBe(0);
    expect(useAuctionStore.getState().connection).toBe("live");
  });

  it("still notifies when the value genuinely changes", () => {
    const { setConnection } = useAuctionStore.getState();
    setConnection("live");

    let notifications = 0;
    const unsubscribe = useAuctionStore.subscribe(() => {
      notifications += 1;
    });

    setConnection("reconnecting");
    setConnection("live");

    unsubscribe();
    expect(notifications).toBe(2);
    expect(useAuctionStore.getState().connection).toBe("live");
  });

  it("exposes stable action identities", () => {
    // The fix relies on actions being stable, so handlers can reach them with
    // getState() instead of subscribing.
    const first = useAuctionStore.getState();
    first.setConnection("reconnecting");
    first.noteSeq(5);
    const second = useAuctionStore.getState();

    expect(second.setConnection).toBe(first.setConnection);
    expect(second.setFullState).toBe(first.setFullState);
    expect(second.applyBid).toBe(first.applyBid);
  });
});

describe("noteSeq", () => {
  it("flags a gap when a broadcast was missed", () => {
    const { noteSeq } = useAuctionStore.getState();
    noteSeq(1);
    expect(useAuctionStore.getState().missedMessages).toBe(false);
    noteSeq(2);
    expect(useAuctionStore.getState().missedMessages).toBe(false);
    noteSeq(9);
    expect(useAuctionStore.getState().missedMessages).toBe(true);
  });

  it("ignores a missing sequence number", () => {
    const { noteSeq } = useAuctionStore.getState();
    noteSeq(3);
    noteSeq(undefined);
    expect(useAuctionStore.getState().lastSeq).toBe(3);
    expect(useAuctionStore.getState().missedMessages).toBe(false);
  });
});

describe("setFullState", () => {
  it("clears a gap flag, because a snapshot closes it", () => {
    const { noteSeq, setFullState } = useAuctionStore.getState();
    noteSeq(1);
    noteSeq(50);
    expect(useAuctionStore.getState().missedMessages).toBe(true);

    setFullState({ event_id: 1, status: "active", seq: 50 });
    expect(useAuctionStore.getState().missedMessages).toBe(false);
    expect(useAuctionStore.getState().status).toBe("active");
  });
});


describe("markPlayerSold", () => {
  it("counts a sold player and sale price only once when the event is repeated", () => {
    useAuctionStore.setState({
      activePlayerId: 101,
      teams: [{
        id: 7,
        name: "Team Seven",
        color: "#123456",
        captain_id: null,
        budget: 100000,
        spent: 10000,
        remaining: 90000,
        max_players: 8,
        player_count: 2,
      }],
      players: [{
        id: 101,
        player_id: 501,
        name: "Auction Player",
        base_price: 5000,
        current_bid: 15000,
        current_bid_team_id: 7,
        status: "active",
        version: 2,
      }],
    });

    const { markPlayerSold } = useAuctionStore.getState();
    markPlayerSold(101, 7, 15000);
    markPlayerSold(101, 7, 15000);

    const state = useAuctionStore.getState();
    expect(state.teams[0].player_count).toBe(3);
    expect(state.teams[0].spent).toBe(25000);
    expect(state.teams[0].remaining).toBe(75000);
    expect(state.players[0].status).toBe("sold");
    expect(state.activePlayerId).toBeNull();
  });

  it("does not change team counts for an unknown auction player", () => {
    useAuctionStore.setState({
      teams: [{
        id: 7,
        name: "Team Seven",
        color: "#123456",
        captain_id: null,
        budget: 100000,
        spent: 10000,
        remaining: 90000,
        max_players: 8,
        player_count: 2,
      }],
      players: [],
    });

    useAuctionStore.getState().markPlayerSold(999, 7, 15000);
    expect(useAuctionStore.getState().teams[0].player_count).toBe(2);
    expect(useAuctionStore.getState().teams[0].spent).toBe(10000);
  });
});

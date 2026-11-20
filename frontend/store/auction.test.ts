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

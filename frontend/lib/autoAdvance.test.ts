import { describe, it, expect } from "vitest";
import { shouldAutoAdvance, autoAdvanceDelayMs } from "./autoAdvance";

const base = { enabled: true, status: "active", activePlayerId: null } as const;

describe("shouldAutoAdvance", () => {
  it("fires when the auction is active and the block is clear", () => {
    expect(shouldAutoAdvance({ ...base })).toBe(true);
  });

  it("does not fire when the auctioneer has switched auto off", () => {
    expect(shouldAutoAdvance({ ...base, enabled: false })).toBe(false);
  });

  it("does not fire when the auction is paused", () => {
    expect(shouldAutoAdvance({ ...base, status: "paused" })).toBe(false);
  });

  it("does not fire when the auction has not started", () => {
    expect(shouldAutoAdvance({ ...base, status: "scheduled" })).toBe(false);
  });

  it("does not fire when the auction has ended", () => {
    expect(shouldAutoAdvance({ ...base, status: "completed" })).toBe(false);
  });

  it("does not fire when status is unknown", () => {
    expect(shouldAutoAdvance({ ...base, status: null })).toBe(false);
    expect(shouldAutoAdvance({ ...base, status: undefined })).toBe(false);
  });

  it("does not fire while a player is still on the block", () => {
    expect(shouldAutoAdvance({ ...base, activePlayerId: 42 })).toBe(false);
  });

  it("treats player id 0 as a player on the block, not an empty block", () => {
    expect(shouldAutoAdvance({ ...base, activePlayerId: 0 })).toBe(false);
  });

  it("does not fire while a Right-to-Match decision is outstanding", () => {
    expect(
      shouldAutoAdvance({ ...base, rtm: { auction_player_id: 7, stage: "offer" } })
    ).toBe(false);
  });

  it("fires once the RTM state has cleared", () => {
    expect(shouldAutoAdvance({ ...base, rtm: null })).toBe(true);
  });
});

describe("autoAdvanceDelayMs", () => {
  it("gives a sale time for the SOLD card to land", () => {
    expect(autoAdvanceDelayMs("sold")).toBe(2500);
    expect(autoAdvanceDelayMs("unsold")).toBe(2500);
  });

  it("brings the first player up promptly on start", () => {
    expect(autoAdvanceDelayMs("start")).toBeLessThan(1000);
  });
});

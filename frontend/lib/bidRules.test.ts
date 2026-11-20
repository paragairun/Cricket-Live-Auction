/**
 * Tests for the bid-increment rules.
 *
 * New in 2.0.0 (2026-11-20). The frontend previously had no test tooling at all
 * despite the README documenting `npm test`.
 *
 * The first block is the regression guard for the client/server divergence: the
 * captain UI used a hardcoded ladder (>=1000 -> 100, >=10000 -> 1000,
 * >=100000 -> 10000) while the server validated against the event's configured
 * BidSlab rows. An organizer who set a 500 increment gave bidders +100 buttons
 * that the server rejected. These assertions pin the UI to the configured
 * slabs, and mirror backend/tests/test_auction_engine.py.
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_INCREMENT,
  incrementFor,
  maxAllowedBid,
  quickBidSteps,
  reservedBudget,
  validateBid,
} from "./bidRules";
import type { BidSlab } from "@/store/auction";

const slab = (
  min_value: number,
  max_value: number,
  increment: number,
  sort_order = 0
): BidSlab => ({ id: sort_order, min_value, max_value, increment, sort_order });

describe("incrementFor", () => {
  it("uses the configured slab, not a hardcoded ladder", () => {
    const slabs = [slab(0, 50000, 500, 0), slab(50000, 0, 5000, 1)];
    // The old hardcoded ladder would have said 1000 here. The organizer
    // configured 500, so 500 is the answer.
    expect(incrementFor(slabs, 10000)).toBe(500);
    expect(incrementFor(slabs, 49999)).toBe(500);
    expect(incrementFor(slabs, 50000)).toBe(5000);
    expect(incrementFor(slabs, 250000)).toBe(5000);
  });

  it("treats max_value as exclusive, matching the server", () => {
    const slabs = [slab(0, 1000, 100, 0), slab(1000, 0, 1000, 1)];
    expect(incrementFor(slabs, 999)).toBe(100);
    // At exactly max_value the next slab takes over. The 1.x local copy used
    // `<=` here and would have returned 100.
    expect(incrementFor(slabs, 1000)).toBe(1000);
  });

  it("falls back to the server's default when no slab matches", () => {
    expect(incrementFor([], 5000)).toBe(DEFAULT_INCREMENT);
    expect(incrementFor([slab(100000, 0, 10000)], 500)).toBe(DEFAULT_INCREMENT);
  });

  it("respects sort_order when slabs overlap", () => {
    const slabs = [slab(0, 0, 2500, 1), slab(0, 20000, 500, 0)];
    expect(incrementFor(slabs, 10000)).toBe(500);
  });
});

describe("quickBidSteps", () => {
  it("offers 1x, 2x and 5x the configured step", () => {
    expect(quickBidSteps([slab(0, 0, 500)], 10000)).toEqual([500, 1000, 2500]);
  });
});

describe("maxAllowedBid", () => {
  it("caps a single jump at the lesser of 50% of the bid and 5% of the purse", () => {
    // 50% of 10000 = 5000; 5% of 100000 = 5000 -> +5000
    expect(maxAllowedBid(10000, 100000)).toBe(15000);
    // 50% of 10000 = 5000; 5% of 20000 = 1000 -> +1000
    expect(maxAllowedBid(10000, 20000)).toBe(11000);
  });
});

describe("reservedBudget", () => {
  it("reserves for the slots still needed to reach the minimum squad", () => {
    // Minimum 5, none bought, this player fills one -> 4 slots to reserve.
    expect(reservedBudget(5, 0, 1000)).toBe(4000);
    expect(reservedBudget(5, 3, 1000)).toBe(1000);
    expect(reservedBudget(5, 4, 1000)).toBe(0);
    expect(reservedBudget(5, 9, 1000)).toBe(0);
  });

  it("reserves nothing when no minimum is configured", () => {
    expect(reservedBudget(0, 0, 1000)).toBe(0);
  });
});

describe("validateBid", () => {
  const base = {
    currentBid: 10000,
    hasExistingBid: true,
    basePrice: 1000,
    slabs: [slab(0, 0, 1000)],
    teamBudget: 100000,
    teamSpent: 0,
    teamSquadSize: 2,
    teamMaxPlayers: 11,
    minSquad: 0,
    eventBasePrice: 1000,
  };

  it("accepts a bid one full increment above the current price", () => {
    expect(validateBid({ ...base, amount: 11000 })).toBeNull();
  });

  it("rejects a bid at or below the current price", () => {
    expect(validateBid({ ...base, amount: 10000 })).toMatch(/higher than/);
    expect(validateBid({ ...base, amount: 9000 })).toMatch(/higher than/);
  });

  it("rejects an increment below the slab step", () => {
    expect(validateBid({ ...base, amount: 10500 })).toMatch(/Minimum increment/);
  });

  it("rejects an increment that is not a multiple of the step", () => {
    expect(validateBid({ ...base, amount: 11500 })).toMatch(/multiple of/);
  });

  it("rejects a bid beyond the single-jump ceiling", () => {
    // Guards against a mistyped extra zero.
    expect(validateBid({ ...base, amount: 100000 })).toMatch(/ceiling/);
  });

  it("rejects a bid beyond the remaining purse", () => {
    expect(
      validateBid({ ...base, amount: 11000, teamBudget: 20000, teamSpent: 15000 })
    ).toMatch(/Insufficient purse/);
  });

  it("rejects a bid that would break the minimum-squad reserve", () => {
    // Chosen to clear the earlier checks so the reserve rule is what fires:
    // remaining = 15000, single-jump ceiling = 15000, reserve = 4 x 1000, so
    // anything above 11000 must be refused.
    const args = {
      ...base,
      teamBudget: 100000,
      teamSpent: 85000,
      teamSquadSize: 0,
      minSquad: 5,
      eventBasePrice: 1000,
    };
    expect(validateBid({ ...args, amount: 12000 })).toMatch(/minimum squad/);
    // Exactly at the reserve boundary is legal.
    expect(validateBid({ ...args, amount: 11000 })).toBeNull();
  });

  it("rejects a bid when the roster is full", () => {
    expect(
      validateBid({ ...base, amount: 11000, teamSquadSize: 11, teamMaxPlayers: 11 })
    ).toMatch(/roster is full/);
  });

  it("requires the base price for an opening bid", () => {
    const opening = { ...base, hasExistingBid: false, basePrice: 5000, currentBid: 5000 };
    expect(validateBid({ ...opening, amount: 4000 })).toMatch(/base price/);
    expect(validateBid({ ...opening, amount: 5000 })).toBeNull();
  });

  it("rejects a non-positive amount", () => {
    expect(validateBid({ ...base, amount: 0 })).toMatch(/positive/);
    expect(validateBid({ ...base, amount: -5000 })).toMatch(/positive/);
  });
});

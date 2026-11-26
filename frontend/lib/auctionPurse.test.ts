import { describe, expect, it } from "vitest";
import { getMinimumAuctionBasePrice, getRemainingAuctionReserveSlots } from "./auctionPurse";

describe("getMinimumAuctionBasePrice", () => {
  const effectivePrice = (player: { base_price: number; category_id?: number | null }) =>
    player.category_id === 10 ? 250000 : player.base_price;

  it("does not reserve purse using a captain-only category price", () => {
    const players = [
      { player_id: 1, base_price: 500000, category_id: 10 }, // assigned captain
      { player_id: 2, base_price: 500000, category_id: null },
      { player_id: 3, base_price: 500000, category_id: null },
    ];

    expect(
      getMinimumAuctionBasePrice(players, new Set([1]), effectivePrice)
    ).toBe(500000);
  });

  it("still uses a category price when an eligible auction player belongs to it", () => {
    const players = [
      { player_id: 1, base_price: 500000, category_id: 10 }, // assigned captain
      { player_id: 2, base_price: 500000, category_id: 10 }, // eligible player
      { player_id: 3, base_price: 500000, category_id: null },
    ];

    expect(
      getMinimumAuctionBasePrice(players, new Set([1]), effectivePrice)
    ).toBe(250000);
  });

  it("uses the fallback when there are no eligible auction players", () => {
    const players = [
      { player_id: 1, base_price: 250000, category_id: 10 },
    ];

    expect(
      getMinimumAuctionBasePrice(players, new Set([1]), effectivePrice)
    ).toBe(1000);
  });
});

describe("getRemainingAuctionReserveSlots", () => {
  it("does not let a free assigned captain reduce the required auction-player reserve", async () => {
    // Seven paid players are required. The current bid is for the first;
    // six more must be reserved at their base price.
    expect(getRemainingAuctionReserveSlots(7, 1, true)).toBe(6);
  });

  it("reduces the reserve after an auction player has been purchased", () => {
    expect(getRemainingAuctionReserveSlots(7, 2, true)).toBe(5);
  });

  it("works normally for a team without an assigned captain", () => {
    expect(getRemainingAuctionReserveSlots(7, 0, false)).toBe(6);
  });
});

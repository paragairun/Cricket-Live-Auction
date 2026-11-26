import { describe, expect, it } from "vitest";
import { getMinimumAuctionBasePrice } from "./auctionPurse";

describe("getMinimumAuctionBasePrice", () => {
  const effectivePrice = (player: { base_price: number; category_id?: number | null }) =>
    player.category_id === 10 ? 900000 : player.base_price;

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
    ).toBe(500000);
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

export type AuctionBasePricePlayer = {
  player_id: number;
  base_price: number;
  category_id?: number | null;
};

/**
 * Minimum base price among players who are eligible to appear in the auction.
 * Assigned captains are free roster slots, so their category price must not
 * influence the purse reserve for future auction purchases.
 */
export function getMinimumAuctionBasePrice(
  players: AuctionBasePricePlayer[],
  captainIds: ReadonlySet<number>,
  getEffectiveBasePrice: (player: AuctionBasePricePlayer) => number,
  fallback = 1000
): number {
  const prices = players
    .filter((player) => !captainIds.has(Number(player.player_id)))
    .map(getEffectiveBasePrice)
    .filter((price) => Number.isFinite(price) && price > 0);

  return prices.length > 0 ? Math.min(...prices) : fallback;
}

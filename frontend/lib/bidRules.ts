/**
 * Bid increment rules, derived from the server's configured slabs.
 *
 * New in 2.0.0 (2026-11-20).
 *
 * This file deliberately contains NO hardcoded ladder. 1.x had one in the
 * captain page:
 *
 *   if (currentBid >= 100000) return 10000;
 *   if (currentBid >= 10000)  return 1000;
 *   if (currentBid >= 1000)   return 100;
 *
 * while the server validated against the event's configurable BidSlab rows. An
 * organizer who set a slab increment of 500 handed their bidders quick-bid
 * buttons of +100/+200/+500 that the server then rejected with "Minimum
 * increment is 500" — mid-auction, with the clock running.
 *
 * Every function here takes the slabs from the auction state payload, so the UI
 * can only ever offer what the server will accept. Mirrors
 * backend/app/services/bid_rules.py.
 */
import type { BidSlab } from "@/store/auction";

export const DEFAULT_INCREMENT = 1000;

function slabContains(slab: BidSlab, amount: number): boolean {
  if (amount < slab.min_value) return false;
  if (slab.max_value && amount >= slab.max_value) return false;
  return true;
}

/** Minimum legal step above `currentBid`, per the event's configured slabs. */
export function incrementFor(slabs: BidSlab[], currentBid: number): number {
  const ordered = [...slabs].sort(
    (a, b) => a.sort_order - b.sort_order || a.min_value - b.min_value
  );
  for (const slab of ordered) {
    if (slabContains(slab, currentBid)) return Math.max(1, slab.increment);
  }
  return DEFAULT_INCREMENT;
}

/** The 1x / 2x / 5x multiples a UI offers as one-tap buttons. */
export function quickBidSteps(slabs: BidSlab[], currentBid: number): number[] {
  const step = incrementFor(slabs, currentBid);
  return [step, step * 2, step * 5];
}

/** Ceiling on a single jump, to catch a mistyped extra zero. */
export function maxAllowedBid(currentBid: number, teamBudget: number): number {
  const fiftyPercent = Math.floor(currentBid / 2);
  const fivePercentBudget = Math.floor(teamBudget / 20);
  return currentBid + Math.max(0, Math.min(fiftyPercent, fivePercentBudget));
}

/** Purse a team must keep back to fill its minimum squad. */
export function reservedBudget(
  minSquad: number,
  currentSquad: number,
  basePrice: number
): number {
  if (minSquad <= 0) return 0;
  const remainingSlots = Math.max(0, minSquad - currentSquad - 1);
  return remainingSlots * Math.max(0, basePrice);
}

export interface ValidateArgs {
  amount: number;
  currentBid: number;
  hasExistingBid: boolean;
  basePrice: number;
  slabs: BidSlab[];
  teamBudget: number;
  teamSpent: number;
  teamSquadSize: number;
  teamMaxPlayers: number;
  minSquad: number;
  eventBasePrice: number;
}

/**
 * Returns an error message, or null when the bid is legal.
 * Same checks, same order, same wording as the server, so a bid the UI accepts
 * is a bid the server accepts.
 */
export function validateBid(args: ValidateArgs): string | null {
  const {
    amount,
    currentBid,
    hasExistingBid,
    basePrice,
    slabs,
    teamBudget,
    teamSpent,
    teamSquadSize,
    teamMaxPlayers,
    minSquad,
    eventBasePrice,
  } = args;

  if (!Number.isFinite(amount) || amount <= 0) return "Bid must be a positive amount";
  if (teamSquadSize >= teamMaxPlayers) return "Team roster is full";

  if (!hasExistingBid) {
    if (amount < basePrice) {
      return `Bid must be at least the base price (${basePrice})`;
    }
  } else {
    if (amount <= currentBid) {
      return `Bid must be higher than the current bid (${currentBid})`;
    }
    const step = incrementFor(slabs, currentBid);
    const increase = amount - currentBid;
    if (increase < step) return `Minimum increment at this price is ${step}`;
    if (increase % step !== 0) return `Bid increment must be a multiple of ${step}`;

    const ceiling = maxAllowedBid(currentBid, teamBudget);
    if (amount > ceiling) {
      return `Single-bid ceiling is ${ceiling} — check for a mistyped digit`;
    }
  }

  const remaining = teamBudget - teamSpent;
  if (amount > remaining) return `Insufficient purse. Remaining: ${remaining}`;

  const reserve = reservedBudget(minSquad, teamSquadSize, eventBasePrice);
  if (reserve && amount > remaining - reserve) {
    return (
      `Must keep ${reserve} back to fill the minimum squad of ${minSquad}. ` +
      `Maximum bid here is ${Math.max(0, remaining - reserve)}`
    );
  }

  return null;
}

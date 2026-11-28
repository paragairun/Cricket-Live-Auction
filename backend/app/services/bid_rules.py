"""Bid increment rules — the single source of truth.

New in 2.0.0 (2026-11-20).

Previously the increment ladder existed twice: the server validated against the
event's configurable BidSlab rows, while the captain UI used a hardcoded ladder
(>=100000 -> 10000, >=10000 -> 1000, >=1000 -> 100). An organizer who configured
a slab of 500 gave their bidders quick-bid buttons of +100/+200/+500 that the
server then rejected with "Minimum increment is 500".

The rules live here, and the computed ladder is published in the auction state
payload so every client renders exactly what the server will accept.
"""
from dataclasses import dataclass

DEFAULT_INCREMENT = 1000


@dataclass(frozen=True)
class Slab:
    min_value: int
    max_value: int  # exclusive; 0 means no upper bound
    increment: int
    sort_order: int = 0

    def contains(self, amount: int) -> bool:
        if amount < self.min_value:
            return False
        if self.max_value and amount >= self.max_value:
            return False
        return True


def increment_for(slabs: list[Slab], current_bid: int) -> int:
    """Minimum legal step above `current_bid`."""
    for slab in sorted(slabs, key=lambda s: (s.sort_order, s.min_value)):
        if slab.contains(current_bid):
            return max(1, slab.increment)
    return DEFAULT_INCREMENT


def quick_bid_steps(slabs: list[Slab], current_bid: int) -> list[int]:
    """The multiples a UI should offer as one-tap buttons: 1x, 2x and 5x."""
    step = increment_for(slabs, current_bid)
    return [step, step * 2, step * 5]


def max_allowed_bid(current_bid: int, team_budget: int) -> int:
    """Ceiling on a single jump, to catch a mistyped extra zero.

    min(50% of the current bid, 5% of the team's total purse) — unchanged from
    1.x, but now computed in one place rather than reimplemented in the UI.
    """
    fifty_percent = current_bid // 2
    five_percent_budget = team_budget // 20
    return current_bid + max(0, min(fifty_percent, five_percent_budget))


def reserved_budget(min_squad: int, current_squad: int, base_price: int) -> int:
    """Purse a team must keep back to fill its minimum squad.

    New in 2.0.0. Nothing previously stopped a team spending 95% of its purse on
    two players and then being unable to field an XI.
    """
    if min_squad <= 0:
        return 0
    # -1 because the player currently being bid on fills one of the slots.
    remaining_slots = max(0, min_squad - current_squad - 1)
    return remaining_slots * max(0, base_price)


def validate_bid(
    *,
    amount: int,
    current_bid: int,
    has_existing_bid: bool,
    base_price: int,
    slabs: list[Slab],
    team_budget: int,
    team_spent: int,
    team_squad_size: int,
    team_max_players: int,
    min_squad: int,
    event_base_price: int,
    allow_same_price: bool = False,
) -> str | None:
    """Return an error message, or None when the bid is legal.

    Pure function: no database, no clock. This is what the concurrency test
    harness and the UI both exercise.
    """
    if amount <= 0:
        return "Bid must be a positive amount"

    if team_squad_size >= team_max_players:
        return "Team roster is full"

    if not has_existing_bid:
        if amount < base_price:
            return f"Bid must be at least the base price ({base_price})"
    else:
        if amount < current_bid or (amount == current_bid and not allow_same_price):
            return f"Bid must be higher than the current bid ({current_bid})"
        if amount > current_bid:
            step = increment_for(slabs, current_bid)
            increase = amount - current_bid
            if increase < step:
                return f"Minimum increment at this price is {step}"
            if increase % step != 0:
                return f"Bid increment must be a multiple of {step}"

            ceiling = max_allowed_bid(current_bid, team_budget)
            if amount > ceiling:
                return f"Single-bid ceiling is {ceiling} — check for a mistyped digit"

    remaining = team_budget - team_spent
    if amount > remaining:
        return f"Insufficient purse. Remaining: {remaining}"

    reserve = reserved_budget(min_squad, team_squad_size, event_base_price)
    if reserve and amount > remaining - reserve:
        return (
            f"Must keep {reserve} back to fill the minimum squad of {min_squad}. "
            f"Maximum bid here is {max(0, remaining - reserve)}"
        )

    return None

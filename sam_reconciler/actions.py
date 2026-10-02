"""Turns the license position into a short, ordered to-do list, the part a
SAM manager actually takes into a meeting."""
from __future__ import annotations

from dataclasses import dataclass

from .models import METRIC_LABELS, PER_CORE, Position
from .reconcile import Result


@dataclass
class Action:
    priority: int  # 1 = do first
    kind: str  # buy, reclaim, renew, reduce, reassign, remove, review
    product: str
    text: str
    amount: float = 0.0  # dollars at stake


def _unit(p: Position, n: int) -> str:
    if p.metric == PER_CORE:
        return f"{n} core{'s' if n != 1 else ''}"
    if p.metric == "per_user":
        return f"{n} user{'s' if n != 1 else ''}"
    return f"{n} license{'s' if n != 1 else ''}"


def build_actions(result: Result) -> list[Action]:
    actions: list[Action] = []
    today = result.as_of

    for p in result.positions:
        short_now, short_after = max(0, -p.balance), max(0, -p.balance_after_reclaim)
        surplus_after = max(0, p.balance_after_reclaim)

        if p.status == "Unlicensed":
            actions.append(Action(1, "remove", p.product,
                                  f"{p.product} is on {p.consumed} devices with no license on record. Find the purchase "
                                  f"records, or uninstall it" + (f" (start with the {p.reclaimable} unused copies)"
                                  if p.reclaimable else "") + f". Buying would take {_unit(p, p.consumed)}.", p.exposure))
            continue

        if short_now and p.reclaimable:
            used = min(p.reclaimable, short_now)
            actions.append(Action(1 if short_after == 0 else 2, "reclaim", p.product,
                                  f"Reclaim {p.reclaimable} unused {p.product} installs (no use in "
                                  f"{result.settings.unused_days}+ days). That covers {used} of the {short_now} you're short.",
                                  p.exposure - p.exposure_after_reclaim))
        if short_after:
            actions.append(Action(2, "buy", p.product,
                                  f"Buy {_unit(p, short_after)} of {p.product} ({METRIC_LABELS[p.metric].lower()}), "
                                  f"or remove it where it isn't needed.", p.exposure_after_reclaim))

        if p.expired_rights:
            actions.append(Action(2, "review", p.product,
                                  f"{p.expired_rights} {p.product} rights have expired and no longer count. "
                                  f"Renew them or confirm they were meant to lapse.", p.expired_rights * p.unit_price))

        if p.renewal_date and (p.renewal_date - today).days <= result.settings.expiring_days:
            need = p.consumed - p.reclaimable
            actions.append(Action(1, "renew", p.product,
                                  f"{p.product} renews in {(p.renewal_date - today).days} days. You own {p.entitled} "
                                  f"and need about {need} after reclaim, so renew {need} instead of {p.entitled}."
                                  if need < p.entitled else
                                  f"{p.product} renews in {(p.renewal_date - today).days} days and you need "
                                  f"{need}, more than the {p.entitled} you own. Add the extra at renewal.",
                                  abs(p.entitled - need) * p.unit_price))
        elif surplus_after:
            if p.renewal_date:
                actions.append(Action(3, "reduce", p.product,
                                      f"Cut {p.product} by {_unit(p, surplus_after)} at the next renewal "
                                      f"({p.renewal_date:%b %d, %Y}).", p.shelfware))
            else:
                actions.append(Action(3, "reassign", p.product,
                                      f"{_unit(p, surplus_after).capitalize()} of {p.product} are unused. "
                                      f"Assign them before buying more.", p.shelfware))

    if result.unmatched:
        actions.append(Action(3, "review", "Unrecognized software",
                              f"{len(result.unmatched)} titles ({sum(result.unmatched.values())} installs) didn't match "
                              f"a product rule. Add rules for the commercial ones so they get reconciled."))

    # Highest-stakes product first, and within a product: reclaim, then buy.
    at_stake = {p.product: max(p.exposure, p.shelfware) for p in result.positions}
    order = {"renew": 0, "remove": 0, "reclaim": 1, "buy": 2, "review": 3, "reduce": 4, "reassign": 4}
    first = {}
    for a in actions:
        first[a.product] = min(first.get(a.product, 9), a.priority)
    actions.sort(key=lambda a: (first[a.product], -at_stake.get(a.product, 0), a.product, order[a.kind]))
    return actions

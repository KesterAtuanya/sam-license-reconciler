"""The reconciliation itself: how many rights each product needs, how many
are owned, what can be reclaimed, and what it all costs."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .models import (
    PER_CORE, PER_DEVICE, PER_USER, Dataset, Device, Entitlement, Install, Position, Product,
    ReclaimCandidate,
)
from .normalize import Rule, normalize_installs

UNKNOWN_CORES_FALLBACK = 4


@dataclass
class Settings:
    unused_days: int = 90  # installs not used in this many days can be reclaimed
    expiring_days: int = 90  # warn about entitlements ending this soon


@dataclass
class ExpiringEntitlement:
    entitlement: Entitlement
    days_left: int


@dataclass
class Result:
    positions: list[Position]
    unmatched: Counter  # display names no rule recognized
    free_installs: Counter  # freeware product -> install count
    expiring: list[ExpiringEntitlement]
    devices: int
    installs: int
    as_of: date
    source: str
    settings: Settings
    notes: list[str] = field(default_factory=list)

    @property
    def exposure(self) -> float:
        return sum(p.exposure for p in self.positions)

    @property
    def exposure_after_reclaim(self) -> float:
        return sum(p.exposure_after_reclaim for p in self.positions)

    @property
    def shelfware(self) -> float:
        return sum(p.shelfware for p in self.positions)

    @property
    def reclaim_value(self) -> float:
        return sum(c.value for p in self.positions for c in p.reclaim)

    @property
    def reclaim_count(self) -> int:
        return sum(p.reclaimable for p in self.positions)


def licensable_cores(device: Device, product: Product) -> int:
    """Cores to license on one device: apply the core factor, the per-device
    minimum, and round up to whole 2-core packs."""
    cores = device.cores or UNKNOWN_CORES_FALLBACK
    needed = max(product.min_cores, math.ceil(cores * product.core_factor))
    return needed + (needed % 2)


def _user_key(device: Device | None, device_id: str) -> str:
    if device and device.assigned_user:
        return device.assigned_user.strip().lower()
    return f"(unassigned device {device_id})"


def _unit_price(product: Product, ents: list[Entitlement]) -> float:
    paid = [e for e in ents if e.unit_cost > 0 and e.rights > 0]
    if paid:
        return round(sum(e.unit_cost * e.rights for e in paid) / sum(e.rights for e in paid), 2)
    return product.unit_price


def _metric(product: Product, ents: list[Entitlement]) -> str:
    """The entitlements decide the metric when they say something different
    from the catalog, because that is what was actually bought."""
    if not ents:
        return product.metric
    by_rights: Counter = Counter()
    for e in ents:
        by_rights[e.metric] += max(e.rights, 1)
    return by_rights.most_common(1)[0][0]


def reconcile(data: Dataset, rules: list[Rule], settings: Settings | None = None) -> Result:
    settings = settings or Settings()
    today = data.as_of
    cutoff = today - timedelta(days=settings.unused_days)

    unmatched = normalize_installs(data.installs, rules)
    devices = {d.device_id: d for d in data.devices}
    catalog = {p.name: p for p in data.products}

    installs_by_product: dict[str, list[Install]] = defaultdict(list)
    for inst in data.installs:
        if inst.product:
            installs_by_product[inst.product].append(inst)

    ents_by_product: dict[str, list[Entitlement]] = defaultdict(list)
    for e in data.entitlements:
        ents_by_product[e.product].append(e)

    # Products seen anywhere: installs, entitlements or the catalog.
    names = set(installs_by_product) | set(ents_by_product)
    free_installs: Counter = Counter()
    positions: list[Position] = []
    notes: list[str] = []
    unknown_core_devices: set[str] = set()

    for name in sorted(names):
        installs = installs_by_product.get(name, [])
        product = catalog.get(name) or Product(
            name, installs[0].publisher if installs else "", ents_by_product[name][0].metric if ents_by_product.get(name) else PER_DEVICE
        )
        if product.free:
            free_installs[name] = len(installs)
            continue

        all_ents = ents_by_product.get(name, [])
        active = [e for e in all_ents if e.active_on(today)]
        expired = [e for e in all_ents if e.end_date and e.end_date < today]
        metric = _metric(product, active or all_ents)
        price = _unit_price(product, active or all_ents)

        # Group installs by device (one device can report a product twice).
        by_device: dict[str, list[Install]] = defaultdict(list)
        for inst in installs:
            by_device[inst.device_id].append(inst)

        def last_used(group: list[Install]):
            dates = [i.last_used for i in group]
            return None if any(d is None for d in dates) else max(dates)

        reclaim: list[ReclaimCandidate] = []
        no_usage = sum(1 for i in installs if i.last_used is None)

        if metric == PER_USER:
            by_user: dict[str, list[str]] = defaultdict(list)
            for dev_id in by_device:
                by_user[_user_key(devices.get(dev_id), dev_id)].append(dev_id)
            consumed = len(by_user)
            for user, dev_ids in by_user.items():
                group = [i for d in dev_ids for i in by_device[d]]
                lu = last_used(group)
                if lu is not None and lu < cutoff:
                    dev = devices.get(dev_ids[0])
                    reclaim.append(ReclaimCandidate(name, dev_ids[0], dev.name if dev else dev_ids[0], user,
                                                    lu, (today - lu).days, price))
        elif metric == PER_CORE:
            consumed = 0
            for dev_id in by_device:
                dev = devices.get(dev_id) or Device(dev_id, dev_id)
                if not dev.cores:
                    unknown_core_devices.add(dev.name)
                consumed += licensable_cores(dev, product)
        else:  # per device and site
            consumed = len(by_device)
            if metric == PER_DEVICE:
                for dev_id, group in by_device.items():
                    lu = last_used(group)
                    if lu is not None and lu < cutoff:
                        dev = devices.get(dev_id)
                        reclaim.append(ReclaimCandidate(name, dev_id, dev.name if dev else dev_id,
                                                        _user_key(dev, dev_id), lu, (today - lu).days, price))

        reclaim.sort(key=lambda c: -c.days_unused)
        expiring_rights = sum(
            e.rights for e in active if e.end_date and (e.end_date - today).days <= settings.expiring_days
        )
        positions.append(
            Position(
                product=name,
                publisher=product.publisher,
                metric=metric,
                unit_price=price,
                entitled=sum(e.rights for e in active),
                consumed=consumed,
                installs=len(installs),
                reclaimable=len(reclaim),
                no_usage_data=no_usage if metric in (PER_DEVICE, PER_USER) else 0,
                reclaim=reclaim,
                expiring_rights=expiring_rights,
                expired_rights=sum(e.rights for e in expired),
                renewal_date=min(e.end_date for e in active) if active and all(e.end_date for e in active) else None,
            )
        )

    if unknown_core_devices:
        notes.append(
            f"{len(unknown_core_devices)} server(s) had no core count, so {UNKNOWN_CORES_FALLBACK} cores were assumed: "
            + ", ".join(sorted(unknown_core_devices)[:8])
        )

    expiring = sorted(
        (
            ExpiringEntitlement(e, (e.end_date - today).days)
            for e in data.entitlements
            if e.end_date and e.active_on(today) and (e.end_date - today).days <= settings.expiring_days
        ),
        key=lambda x: x.days_left,
    )
    positions.sort(key=lambda p: (-p.exposure, -p.shelfware, p.product))
    return Result(positions, unmatched, free_installs, expiring, len(data.devices), len(data.installs),
                  today, data.source, settings, notes)

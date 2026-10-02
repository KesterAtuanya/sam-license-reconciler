"""Plain data structures used by every part of the tool."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# License metrics the reconciler understands.
PER_DEVICE = "per_device"
PER_USER = "per_user"
PER_CORE = "per_core"
SITE = "site"  # enterprise / site agreement: unlimited use
METRICS = (PER_DEVICE, PER_USER, PER_CORE, SITE)

METRIC_LABELS = {
    PER_DEVICE: "Per device",
    PER_USER: "Per named user",
    PER_CORE: "Per core",
    SITE: "Site / enterprise",
}


@dataclass
class Device:
    device_id: str
    name: str
    kind: str = "workstation"  # workstation, server or vm
    cores: int = 0
    assigned_user: str = ""
    department: str = ""


@dataclass
class Install:
    install_id: str
    device_id: str
    raw_name: str  # display name as discovery reported it
    publisher: str = ""
    version: str = ""
    last_used: Optional[date] = None
    product: str = ""  # normalized product, filled in by normalization


@dataclass
class Product:
    """What the reconciler knows about a licensable product."""

    name: str
    publisher: str
    metric: str = PER_DEVICE
    unit_price: float = 0.0  # per device, per user or per core
    free: bool = False  # freeware: tracked but never needs a license
    core_factor: float = 1.0  # e.g. 0.5 for Oracle on x86
    min_cores: int = 0  # minimum cores licensed per device, e.g. 16 per server


@dataclass
class Entitlement:
    entitlement_id: str
    product: str
    rights: int
    metric: str = PER_DEVICE
    unit_cost: float = 0.0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    po_number: str = ""

    def active_on(self, day: date) -> bool:
        if self.start_date and self.start_date > day:
            return False
        if self.end_date and self.end_date < day:
            return False
        return True


@dataclass
class Dataset:
    devices: list[Device]
    installs: list[Install]
    entitlements: list[Entitlement]
    products: list[Product]
    source: str
    as_of: date


@dataclass
class ReclaimCandidate:
    product: str
    device_id: str
    device_name: str
    user: str
    last_used: date
    days_unused: int
    value: float


@dataclass
class Position:
    """The license position for one product."""

    product: str
    publisher: str
    metric: str
    unit_price: float
    entitled: int  # active rights
    consumed: int  # rights needed by current installs
    installs: int
    reclaimable: int  # rights freed if unused installs are removed
    no_usage_data: int  # installs we couldn't judge
    reclaim: list[ReclaimCandidate] = field(default_factory=list)
    expiring_rights: int = 0  # rights ending within the warning window
    expired_rights: int = 0  # rights already ended (not counted)
    renewal_date: Optional[date] = None  # set when every active right is a subscription

    @property
    def balance(self) -> int:
        """Positive means surplus, negative means shortfall."""
        if self.metric == SITE:
            return 0
        return self.entitled - self.consumed

    @property
    def balance_after_reclaim(self) -> int:
        if self.metric == SITE:
            return 0
        return self.entitled - (self.consumed - self.reclaimable)

    @property
    def exposure(self) -> float:
        return max(0, -self.balance) * self.unit_price

    @property
    def exposure_after_reclaim(self) -> float:
        return max(0, -self.balance_after_reclaim) * self.unit_price

    @property
    def shelfware(self) -> float:
        """Value of rights nobody needs, after reclaim."""
        return max(0, self.balance_after_reclaim) * self.unit_price

    @property
    def status(self) -> str:
        if self.metric == SITE:
            return "Covered"
        if self.entitled == 0:
            return "Unlicensed"
        if self.balance < 0:
            return "Shortfall" if self.balance_after_reclaim < 0 else "Fixable by reclaim"
        if self.balance_after_reclaim > 0:
            return "Surplus"
        return "Compliant"

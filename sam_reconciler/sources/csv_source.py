"""Reads the estate from CSV files, so the tool works with an export from
SAM Pro, SCCM/MECM, Intune, Flexera or a spreadsheet.

Expected files in one folder (column names are case-insensitive):

  devices.csv       device_id, name, kind, cores, assigned_user, department
  installs.csv      install_id, device_id, display_name, publisher, version, last_used
  entitlements.csv  entitlement_id, product, rights, metric, unit_cost, start_date, end_date, po_number
  products.csv      (optional) name, publisher, metric, unit_price, free, core_factor, min_cores

Dates are YYYY-MM-DD or MM/DD/YYYY. Blank last_used means "no usage data".
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..models import METRICS, PER_DEVICE, Dataset, Device, Entitlement, Install, Product

METRIC_ALIASES = {
    "device": "per_device", "per device": "per_device", "per_device": "per_device", "installs": "per_device",
    "user": "per_user", "per user": "per_user", "per_user": "per_user", "named user": "per_user",
    "per named user": "per_user", "subscription": "per_user",
    "core": "per_core", "per core": "per_core", "per_core": "per_core", "processor": "per_core",
    "site": "site", "enterprise": "site", "unlimited": "site",
}


class CsvError(ValueError):
    pass


def parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise CsvError(f"Can't read the date '{value}'. Use YYYY-MM-DD.")


def parse_metric(value: str) -> str:
    key = (value or "").strip().lower()
    if not key:
        return PER_DEVICE
    metric = METRIC_ALIASES.get(key, key)
    if metric in METRICS:
        return metric
    # Fuzzy match for vendor and SAM Pro names like "Per Core (with CAL)".
    for words, guess in ((("core", "processor", "cpu"), "per_core"), (("user",), "per_user"),
                         (("site", "enterprise", "unrestricted", "unlimited"), "site"),
                         (("device", "install", "machine"), "per_device")):
        if any(w in key for w in words):
            return guess
    raise CsvError(f"Unknown license metric '{value}'. Use one of: {', '.join(METRICS)}.")


def _num(value: str, cast=float, default=0):
    value = (value or "").strip().replace(",", "").replace("$", "")
    return cast(value) if value else default


def _rows(path: Path, required: list[str]) -> list[dict]:
    if not path.exists():
        raise CsvError(f"Missing file: {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise CsvError(f"{path.name} is empty.")
        header = {h.strip().lower() for h in reader.fieldnames}
        missing = [c for c in required if c not in header]
        if missing:
            raise CsvError(f"{path.name} is missing column(s): {', '.join(missing)}")
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]


def load_catalog(path: str | Path) -> list[Product]:
    """Read a products.csv: metric, price, core rules and freeware flag per product."""
    return [
        Product(r["name"], r.get("publisher", ""), parse_metric(r.get("metric", "")), _num(r.get("unit_price")),
                r.get("free", "").lower() in ("1", "true", "yes", "y"), _num(r.get("core_factor"), float, 1.0),
                _num(r.get("min_cores"), int))
        for r in _rows(Path(path), ["name"])
    ]


def load_folder(folder: str | Path, as_of: date | None = None) -> Dataset:
    folder = Path(folder)
    devices = [
        Device(r["device_id"], r.get("name") or r["device_id"], r.get("kind") or "workstation",
               _num(r.get("cores"), int), r.get("assigned_user", ""), r.get("department", ""))
        for r in _rows(folder / "devices.csv", ["device_id"])
    ]
    installs = [
        Install(r.get("install_id") or f"row{i}", r["device_id"], r["display_name"], r.get("publisher", ""),
                r.get("version", ""), parse_date(r.get("last_used", "")), r.get("product", ""))
        for i, r in enumerate(_rows(folder / "installs.csv", ["device_id", "display_name"]), start=2)
    ]
    entitlements = [
        Entitlement(r.get("entitlement_id") or f"row{i}", r["product"], _num(r["rights"], int),
                    parse_metric(r.get("metric", "")), _num(r.get("unit_cost")),
                    parse_date(r.get("start_date", "")), parse_date(r.get("end_date", "")), r.get("po_number", ""))
        for i, r in enumerate(_rows(folder / "entitlements.csv", ["product", "rights"]), start=2)
    ]
    products = load_catalog(folder / "products.csv") if (folder / "products.csv").exists() else []
    return Dataset(devices, installs, entitlements, products, f"CSV files in {folder.name}", as_of or date.today())


def write_folder(data: Dataset, folder: str | Path) -> None:
    """Write a dataset out in the CSV layout above. Used to create the
    sample_data folder, which doubles as a template."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    def out(name, header, rows):
        with open(folder / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    iso = lambda d: d.isoformat() if d else ""  # noqa: E731
    out("devices.csv", ["device_id", "name", "kind", "cores", "assigned_user", "department"],
        [[d.device_id, d.name, d.kind, d.cores, d.assigned_user, d.department] for d in data.devices])
    out("installs.csv", ["install_id", "device_id", "display_name", "publisher", "version", "last_used"],
        [[i.install_id, i.device_id, i.raw_name, i.publisher, i.version, iso(i.last_used)] for i in data.installs])
    out("entitlements.csv", ["entitlement_id", "product", "rights", "metric", "unit_cost", "start_date", "end_date", "po_number"],
        [[e.entitlement_id, e.product, e.rights, e.metric, e.unit_cost, iso(e.start_date), iso(e.end_date), e.po_number]
         for e in data.entitlements])
    out("products.csv", ["name", "publisher", "metric", "unit_price", "free", "core_factor", "min_cores"],
        [[p.name, p.publisher, p.metric, p.unit_price, "yes" if p.free else "no", p.core_factor, p.min_cores]
         for p in data.products])

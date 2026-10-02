"""Command-line entry point.

    python -m sam_reconciler --demo --open
    python -m sam_reconciler --csv path/to/folder
    python -m sam_reconciler --live --mapping my_mapping.json
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from datetime import date
from pathlib import Path

from . import __version__
from .actions import build_actions
from .normalize import build_rules
from .reconcile import Settings, reconcile
from .report import money, write_all


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sam-reconcile",
                                description="Work out your software license position, true-up exposure and reclaim savings.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="use built-in sample data")
    src.add_argument("--csv", metavar="FOLDER", help="read devices.csv, installs.csv and entitlements.csv from a folder")
    src.add_argument("--live", action="store_true", help="read from ServiceNow SAM Pro using SN_* environment variables")
    p.add_argument("--mapping", help="JSON file overriding ServiceNow tables and fields (live mode)")
    p.add_argument("--catalog", help="products.csv with metrics, prices, core rules and freeware flags")
    p.add_argument("--rules", help="JSON file with extra normalization rules")
    p.add_argument("--unused-days", type=int, default=90, help="days without use before an install is reclaimable (default 90)")
    p.add_argument("--expiring-days", type=int, default=90, help="warn about renewals this many days out (default 90)")
    p.add_argument("--as-of", help="reconcile as of this date, YYYY-MM-DD (default today; demo uses 2026-10-01)")
    p.add_argument("--out", default="reports", help="output folder (default: reports)")
    p.add_argument("--export-sample", metavar="FOLDER", help="write the demo data as CSV files, to use as a template")
    p.add_argument("--open", action="store_true", help="open the HTML report when done")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    from .sources.csv_source import CsvError, load_catalog, load_folder, write_folder

    try:
        if args.demo:
            from .sources.demo import build_demo_dataset

            data = build_demo_dataset(as_of=as_of)
            if args.export_sample:
                write_folder(data, args.export_sample)
                print(f"Sample CSV files written to {args.export_sample}")
        elif args.csv:
            data = load_folder(args.csv, as_of)
        else:
            _load_dotenv()
            from .sources.servicenow import ServiceNowClient, ServiceNowError, load_mapping

            try:
                client = ServiceNowClient.from_env()
                print(f"Reading SAM data from {client.host} ...")
                data = client.load(load_mapping(args.mapping), as_of)
            except ServiceNowError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 2

        if args.catalog:
            catalog = load_catalog(args.catalog)
            known = {p.name for p in catalog}
            data.products = catalog + [p for p in data.products if p.name not in known]
    except (CsvError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    result = reconcile(data, build_rules(args.rules), Settings(args.unused_days, args.expiring_days))
    paths = write_all(result, Path(args.out))

    print()
    print(f"  Compliance exposure today:   {money(result.exposure):>12}")
    print(f"  Exposure after reclaim:      {money(result.exposure_after_reclaim):>12}")
    print(f"  Reclaimable installs:        {result.reclaim_count:>12}   ({money(result.reclaim_value)})")
    print(f"  Surplus rights:              {money(result.shelfware):>12}")
    print()
    for pos in result.positions[:8]:
        print(f"  {pos.product[:38]:<38} {pos.status:<19} {pos.balance:>+6}  -> {pos.balance_after_reclaim:>+6}")
    actions = build_actions(result)
    if actions:
        print("\n  First actions:")
        for a in actions[:3]:
            print(f"   - {a.text}")
    print()
    for kind, path in paths.items():
        print(f"  {kind.upper():<9} {path}")
    if args.open:
        webbrowser.open(paths["html"].resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())

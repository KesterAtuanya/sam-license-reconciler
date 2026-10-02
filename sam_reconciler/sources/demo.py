"""A repeatable sample estate for a fictional company, so anyone can run the
tool without SAM Pro. Each product is set up to tell a different story:
a shortfall that reclaim fixes, one it doesn't, shelfware, an unlicensed
title, a per-core server product, and a lapsed renewal.

Prices are illustrative round numbers for the demo, not vendor quotes.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from ..models import (
    PER_CORE, PER_DEVICE, PER_USER, SITE, Dataset, Device, Entitlement, Install, Product,
)

FIRST = ["ana", "ben", "carla", "dev", "elena", "femi", "grace", "hiro", "ivan", "jade", "kofi", "lena",
         "marco", "nina", "omar", "priya", "quinn", "rosa", "sam", "tara", "uche", "vera", "wes", "yara", "zane"]
LAST = ["adams", "baker", "chen", "diaz", "eze", "fox", "garcia", "hughes", "ito", "james", "khan", "lopez",
        "mensah", "nguyen", "obi", "patel", "reyes", "smith", "tran", "walker"]
DEPTS = ["Operations", "Finance", "Engineering", "Sales", "Dispatch", "IT", "HR", "Marketing"]

# name, publisher, metric, illustrative unit price, free, core factor, min cores
CATALOG = [
    ("Microsoft Visio Professional 2021", "Microsoft", PER_DEVICE, 580, False, 1.0, 0),
    ("Microsoft Project Professional 2021", "Microsoft", PER_DEVICE, 1130, False, 1.0, 0),
    ("Adobe Acrobat Pro", "Adobe", PER_USER, 240, False, 1.0, 0),
    ("Adobe Creative Cloud All Apps", "Adobe", PER_USER, 1000, False, 1.0, 0),
    ("Autodesk AutoCAD LT", "Autodesk", PER_USER, 560, False, 1.0, 0),
    ("Microsoft SQL Server Enterprise 2022", "Microsoft", PER_CORE, 3600, False, 1.0, 4),
    ("Microsoft SQL Server Standard 2022", "Microsoft", PER_CORE, 950, False, 1.0, 4),
    ("Windows Server 2022 Datacenter", "Microsoft", PER_CORE, 385, False, 1.0, 16),
    ("Oracle Database Enterprise Edition", "Oracle", PER_CORE, 47500, False, 0.5, 0),
    ("Tableau Desktop", "Salesforce", PER_USER, 900, False, 1.0, 0),
    ("TechSmith Snagit", "TechSmith", PER_DEVICE, 63, False, 1.0, 0),
    ("WinZip", "Corel", PER_DEVICE, 35, False, 1.0, 0),
    ("Zoom Workplace", "Zoom", SITE, 0, False, 1.0, 0),
    ("7-Zip", "Igor Pavlov", PER_DEVICE, 0, True, 1.0, 0),
    ("Notepad++", "Notepad++ Team", PER_DEVICE, 0, True, 1.0, 0),
    ("Google Chrome", "Google", PER_DEVICE, 0, True, 1.0, 0),
    ("Adobe Acrobat Reader", "Adobe", PER_DEVICE, 0, True, 1.0, 0),
    ("Microsoft SQL Server Express", "Microsoft", PER_DEVICE, 0, True, 1.0, 0),
]

# How each title shows up in discovery: several spellings per product.
DISPLAY_NAMES = {
    "Microsoft Visio Professional 2021": ["Microsoft Visio Professional 2021", "Microsoft Visio Professional 2021 (64-bit)",
                                          "Microsoft Visio Professional 2021 - en-us", "Visio Pro 2021"],
    "Microsoft Project Professional 2021": ["Microsoft Project Professional 2021", "Microsoft Project Professional 2021 - en-us"],
    "Adobe Acrobat Pro": ["Adobe Acrobat Pro DC", "Adobe Acrobat Pro (64-bit)"],
    "Adobe Creative Cloud All Apps": ["Adobe Creative Cloud", "Adobe Creative Cloud Desktop"],
    "Autodesk AutoCAD LT": ["AutoCAD LT 2025", "AutoCAD LT 2024 - English"],
    "Tableau Desktop": ["Tableau Desktop 2024.2", "Tableau Desktop 2025.1"],
    "TechSmith Snagit": ["Snagit 2024", "TechSmith Snagit 2023"],
    "WinZip": ["WinZip 28.0", "WinZip 27 (64-bit)"],
    "Zoom Workplace": ["Zoom Workplace (64-bit)", "Zoom"],
    "7-Zip": ["7-Zip 23.01 (x64)", "7zip 24.08"],
    "Notepad++": ["Notepad++ (64-bit x64)"],
    "Google Chrome": ["Google Chrome"],
    "Adobe Acrobat Reader": ["Adobe Acrobat Reader DC", "Adobe Acrobat Reader (64-bit)"],
    "Microsoft SQL Server Enterprise 2022": ["Microsoft SQL Server 2022 (64-bit) Enterprise Edition: Core-based Licensing"],
    "Microsoft SQL Server Standard 2022": ["Microsoft SQL Server 2022 Standard Edition (64-bit)"],
    "Microsoft SQL Server Express": ["Microsoft SQL Server 2019 Express LocalDB"],
    "Windows Server 2022 Datacenter": ["Microsoft Windows Server 2022 Datacenter"],
    "Oracle Database Enterprise Edition": ["Oracle Database 19c Enterprise Edition"],
}

# Software nobody wrote a rule for yet; shows up as "unrecognized".
UNRECOGNIZED = ["Dispatch Console Client 3.2", "HP Smart", "Lenovo Vantage Service", "Python 3.12.4 (64-bit)",
                "Bloomberg Terminal", "Cisco Secure Client", "Microsoft Edge WebView2 Runtime"]


def build_demo_dataset(seed: int = 7, as_of: date | None = None) -> Dataset:
    as_of = as_of or date(2026, 10, 1)
    r = random.Random(seed)

    # ---- people and devices ----------------------------------------------
    users = [f"{f}.{last}" for f in FIRST for last in LAST]  # 500 unique names
    r.shuffle(users)
    users = users[:480]
    devices: list[Device] = []
    for i, user in enumerate(users):
        kind = "lt" if r.random() < 0.75 else "dt"
        devices.append(Device(f"wk{i:04d}", f"{kind}-{10000 + i * 3}", "workstation", 8, user, r.choice(DEPTS)))
    # 60 people also have a second machine: per-user licensing counts them once.
    for j, user in enumerate(r.sample(users, 60)):
        devices.append(Device(f"wk2{j:03d}", f"dt-{30000 + j}", "workstation", 8, user, "Engineering"))
    # Shared machines with no assigned user (dispatch kiosks).
    for j in range(25):
        devices.append(Device(f"ks{j:03d}", f"ks-dispatch-{j + 1:02d}", "workstation", 4, "", "Dispatch"))
    workstations = list(devices)

    servers: list[Device] = []
    sql_ent_cores = [8, 16, 24, 2]  # the 2-core VM still needs the 4-core minimum
    for i, cores in enumerate(sql_ent_cores):
        servers.append(Device(f"sv-sqle{i}", f"hou-win-sqle-{i + 1:02d}", "server", cores, "", "IT"))
    for i in range(10):
        servers.append(Device(f"sv-sqls{i}", f"dal-win-sql-{i + 1:02d}", "server", r.choice([4, 4, 8]), "", "IT"))
    for i in range(14):
        servers.append(Device(f"sv-host{i}", f"hou-esx-host-{i + 1:02d}", "server", r.choice([16, 24, 32]), "", "IT"))
    servers.append(Device("sv-host-nocores", "atl-esx-host-01", "server", 0, "", "IT"))
    for i in range(2):
        servers.append(Device(f"sv-ora{i}", f"hou-lnx-ora-{i + 1:02d}", "server", 16, "", "IT"))
    devices += servers

    # ---- installs ---------------------------------------------------------
    installs: list[Install] = []
    n = 0

    def recent() -> date:
        return as_of - timedelta(days=r.randint(0, 60))

    def old() -> date:
        return as_of - timedelta(days=r.randint(100, 420))

    def place(product: str, targets: list[Device], unused: int, no_data: int = 0, publisher: str = "") -> None:
        nonlocal n
        for k, dev in enumerate(targets):
            if k < unused:
                lu = old()
            elif k < unused + no_data:
                lu = None
            else:
                lu = recent()
            n += 1
            installs.append(Install(f"in{n:05d}", dev.device_id, r.choice(DISPLAY_NAMES[product]), publisher, "", lu))

    def pick(count: int, pool=None) -> list[Device]:
        return r.sample(pool or workstations, count)

    place("Microsoft Visio Professional 2021", pick(118), unused=31, no_data=4)     # short 18, reclaim fixes it
    place("Microsoft Project Professional 2021", pick(44), unused=6, no_data=2)     # short 14, still short after reclaim
    place("TechSmith Snagit", pick(78), unused=26, no_data=3)                       # short 28, reclaim nearly fixes it
    place("WinZip", pick(142), unused=40, no_data=10)                               # no licenses at all

    # Per-user products: some people have them on both of their machines.
    two_machine_users = {d.assigned_user for d in workstations if d.device_id.startswith("wk2")}
    acro_targets = pick(205, [d for d in workstations if d.assigned_user])
    acro_targets += [d for d in workstations if d.device_id.startswith("wk2")
                     and d.assigned_user in {t.assigned_user for t in acro_targets}][:20]
    acro_targets = list({d.device_id: d for d in acro_targets}.values())
    place("Adobe Acrobat Pro", acro_targets, unused=24, no_data=6)
    cc_targets = pick(38, [d for d in workstations if d.assigned_user and d.assigned_user not in two_machine_users])
    place("Adobe Creative Cloud All Apps", cc_targets, unused=5)
    place("Autodesk AutoCAD LT", pick(12, [d for d in workstations if d.assigned_user]), unused=3)
    place("Tableau Desktop", pick(15, [d for d in workstations if d.assigned_user]), unused=2)

    # Site-licensed and free titles are everywhere.
    place("Zoom Workplace", workstations, unused=0)
    place("Google Chrome", workstations, unused=0)
    place("7-Zip", pick(300), unused=0)
    place("Notepad++", pick(90), unused=0)
    place("Adobe Acrobat Reader", pick(410), unused=0)
    place("Microsoft SQL Server Express", pick(12), unused=0)

    # Servers: usage dates don't apply to per-core products.
    for dev in servers:
        if dev.device_id.startswith("sv-sqle"):
            place("Microsoft SQL Server Enterprise 2022", [dev], unused=0)
        elif dev.device_id.startswith("sv-sqls"):
            place("Microsoft SQL Server Standard 2022", [dev], unused=0)
        elif dev.device_id.startswith("sv-host"):
            place("Windows Server 2022 Datacenter", [dev], unused=0)
        elif dev.device_id.startswith("sv-ora"):
            place("Oracle Database Enterprise Edition", [dev], unused=0)

    for name in UNRECOGNIZED:
        for dev in pick(r.randint(3, 40)):
            n += 1
            installs.append(Install(f"in{n:05d}", dev.device_id, name, "", "", recent()))

    # ---- what the company owns ---------------------------------------------
    y = as_of.year

    def ent(eid, product, rights, metric, cost, start, end=None, po=""):
        return Entitlement(eid, product, rights, metric, cost, start, end, po)

    entitlements = [
        ent("ENT0001", "Microsoft Visio Professional 2021", 60, PER_DEVICE, 560, date(2022, 3, 1), po="PO-22-0148"),
        ent("ENT0002", "Microsoft Visio Professional 2021", 40, PER_DEVICE, 600, date(2023, 9, 15), po="PO-23-0911"),
        ent("ENT0003", "Microsoft Project Professional 2021", 30, PER_DEVICE, 1130, date(2022, 3, 1), po="PO-22-0148"),
        ent("ENT0004", "TechSmith Snagit", 50, PER_DEVICE, 63, date(2024, 1, 10), po="PO-24-0033"),
        ent("ENT0005", "Adobe Acrobat Pro", 250, PER_USER, 240, date(y, 2, 1), date(y + 1, 1, 31), "PO-26-0102"),
        ent("ENT0006", "Adobe Creative Cloud All Apps", 35, PER_USER, 1000, date(y - 1, 11, 15), as_of + timedelta(days=44), "PO-25-1140"),
        ent("ENT0007", "Autodesk AutoCAD LT", 25, PER_USER, 560, date(y, 4, 1), date(y + 1, 3, 31), "PO-26-0390"),
        ent("ENT0008", "Tableau Desktop", 10, PER_USER, 900, date(y, 1, 1), date(y, 12, 31), "PO-26-0007"),
        ent("ENT0009", "Tableau Desktop", 5, PER_USER, 900, date(y - 1, 9, 1), date(y, 8, 31), "PO-25-0870"),
        ent("ENT0010", "Microsoft SQL Server Enterprise 2022", 40, PER_CORE, 3600, date(2023, 6, 30), po="PO-23-0640"),
        ent("ENT0011", "Microsoft SQL Server Standard 2022", 60, PER_CORE, 950, date(2023, 6, 30), po="PO-23-0640"),
        ent("ENT0012", "Windows Server 2022 Datacenter", 352, PER_CORE, 385, date(2023, 6, 30), po="PO-23-0640"),
        ent("ENT0013", "Oracle Database Enterprise Edition", 16, PER_CORE, 47500, date(2021, 5, 20), po="PO-21-0412"),
        ent("ENT0014", "Zoom Workplace", 1, SITE, 0, date(y, 1, 1), date(y, 12, 31), "PO-26-0015"),
    ]

    products = [Product(*row) for row in CATALOG]
    return Dataset(devices, installs, entitlements, products,
                   "Demo data: Northwind Logistics (fictional)", as_of)

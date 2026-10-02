"""Reads installs, devices and entitlements from ServiceNow SAM Pro through
the Table API (read-only GET requests).

Field names differ between releases and between companies that customized
SAM Pro, so every table and field comes from a mapping that can be
overridden with --mapping my_mapping.json. Dot-walked fields such as
"norm_product.name" are resolved by the Table API itself.

Credentials come from environment variables or a git-ignored .env file:
    SN_INSTANCE, and SN_USER + SN_PASSWORD or SN_TOKEN
"""
from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from datetime import date
from typing import Iterator

from ..models import Dataset, Device, Entitlement, Install
from .csv_source import CsvError, parse_date, parse_metric

DEFAULT_MAPPING = {
    "installs": {
        "table": "cmdb_sam_sw_install",
        "query": "norm_productISNOTEMPTY",
        "fields": {
            "install_id": "sys_id",
            "device_id": "installed_on",
            "display_name": "display_name",
            "product": "norm_product.name",
            "publisher": "norm_publisher.name",
            "version": "version",
            # SAM Pro keeps usage in samp_sw_usage, not on the install record.
            # Map a field here if your instance copies it over; otherwise
            # reclaim needs a usage export (see README).
            "last_used": "",
        },
    },
    "devices": {
        "table": "cmdb_ci_computer",
        "fields": {
            "device_id": "sys_id",
            "name": "name",
            "kind": "sys_class_name",
            "cores": "cpu_core_count",
            "assigned_user": "assigned_to.user_name",
            "department": "department.name",
        },
    },
    "entitlements": {
        "table": "alm_license",
        "query": "",
        # true if the cost field holds the total paid for all rights,
        # false if it already holds the price of one right
        "cost_is_total": True,
        "fields": {
            "entitlement_id": "sys_id",
            "product": "software_model.product.name",
            "rights": "rights",
            "metric": "license_metric.name",
            "unit_cost": "cost",
            "start_date": "start_date",
            "end_date": "end_date",
            "po_number": "po_number",
        },
    },
}


class ServiceNowError(RuntimeError):
    pass


def load_mapping(path: str | None) -> dict:
    mapping = deepcopy(DEFAULT_MAPPING)
    if not path:
        return mapping
    with open(path, encoding="utf-8") as fh:
        custom = json.load(fh)
    for section, values in custom.items():
        if section not in mapping:
            raise ServiceNowError(f"Unknown mapping section '{section}'. Use installs, devices or entitlements.")
        for key, value in values.items():
            if key == "fields":
                mapping[section]["fields"].update(value)
            else:
                mapping[section][key] = value
    return mapping


class ServiceNowClient:
    def __init__(self, instance: str, user: str = "", password: str = "", token: str = "",
                 page_size: int = 1000, session=None, timeout: int = 60):
        if not instance:
            raise ServiceNowError("Set SN_INSTANCE to your instance name, for example dev12345.")
        host = instance.replace("https://", "").replace("http://", "").strip("/")
        if "." not in host:
            host += ".service-now.com"
        self.host, self.base = host, f"https://{host}"
        self.page_size, self.timeout = page_size, timeout
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.session.headers.update({"Accept": "application/json"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif user and password:
            self.session.auth = (user, password)
        else:
            raise ServiceNowError("Set SN_USER and SN_PASSWORD, or SN_TOKEN.")

    @classmethod
    def from_env(cls) -> "ServiceNowClient":
        e = os.environ.get
        return cls(e("SN_INSTANCE", ""), e("SN_USER", ""), e("SN_PASSWORD", ""), e("SN_TOKEN", ""))

    def _get(self, table: str, params: dict) -> list[dict]:
        url = f"{self.base}/api/now/table/{table}"
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(min(float(resp.headers.get("Retry-After", 2 ** attempt)), 30))
                continue
            if resp.status_code == 401:
                raise ServiceNowError("ServiceNow rejected the credentials (401).")
            if resp.status_code == 403:
                raise ServiceNowError(f"The account can't read {table} (403). It needs read access to SAM Pro tables, "
                                      "for example the sam_user or asset role.")
            if resp.status_code == 400 and "Invalid table" in resp.text:
                raise ServiceNowError(f"Table {table} doesn't exist on this instance. Is SAM Pro installed? "
                                      "Change the table in your mapping file if you use a different one.")
            if resp.status_code >= 400:
                raise ServiceNowError(f"ServiceNow returned {resp.status_code} for {table}: {resp.text[:200]}")
            return resp.json().get("result", [])
        raise ServiceNowError(f"ServiceNow kept throttling requests to {table}. Try again later.")

    def iter_table(self, table: str, fields: list[str], query: str = "") -> Iterator[dict]:
        offset = 0
        while True:
            rows = self._get(table, {
                "sysparm_fields": ",".join(fields),
                "sysparm_query": (query + "^" if query else "") + "ORDERBYsys_id",
                "sysparm_limit": self.page_size,
                "sysparm_offset": offset,
                "sysparm_exclude_reference_link": "true",
            })
            yield from rows
            if len(rows) < self.page_size:
                return
            offset += self.page_size

    def _section(self, mapping: dict, name: str, query: str = "") -> list[dict]:
        sec = mapping[name]
        fields = {k: v for k, v in sec["fields"].items() if v}
        q = "^".join(x for x in (sec.get("query", ""), query) if x)
        out = []
        for row in self.iter_table(sec["table"], sorted(set(fields.values())), q):
            out.append({k: str(row.get(v) or "") for k, v in fields.items()})
        return out

    def load(self, mapping: dict, as_of: date | None = None) -> Dataset:
        try:
            installs = [
                Install(r.get("install_id", ""), r.get("device_id", ""), r.get("display_name") or r.get("product", ""),
                        r.get("publisher", ""), r.get("version", ""), parse_date(r.get("last_used", "")[:10]),
                        r.get("product", ""))
                for r in self._section(mapping, "installs")
            ]
            device_ids = sorted({i.device_id for i in installs if i.device_id})
            devices: list[Device] = []
            for start in range(0, len(device_ids), 100):
                chunk = device_ids[start:start + 100]
                for r in self._section(mapping, "devices", "sys_idIN" + ",".join(chunk)):
                    cls = r.get("kind", "")
                    devices.append(Device(r.get("device_id", ""), r.get("name", ""),
                                          "server" if "server" in cls else "workstation",
                                          int(float(r.get("cores") or 0)), r.get("assigned_user", ""),
                                          r.get("department", "")))
            total = mapping["entitlements"].get("cost_is_total", True)
            entitlements = []
            for r in self._section(mapping, "entitlements"):
                if not r.get("product"):
                    continue
                rights = int(float(r.get("rights") or 0))
                cost = float(r.get("unit_cost") or 0)
                if total and rights:
                    cost = round(cost / rights, 2)
                entitlements.append(
                    Entitlement(r.get("entitlement_id", ""), r["product"], rights, parse_metric(r.get("metric", "")),
                                cost, parse_date(r.get("start_date", "")[:10]), parse_date(r.get("end_date", "")[:10]),
                                r.get("po_number", "")))
        except CsvError as exc:
            raise ServiceNowError(f"Unexpected value from ServiceNow: {exc}") from exc
        return Dataset(devices, installs, entitlements, [], self.host, as_of or date.today())

"""Writes the results as a self-contained HTML report, CSV files and a JSON summary."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from html import escape
from pathlib import Path

from . import __version__
from .actions import Action, build_actions
from .models import METRIC_LABELS, PER_CORE, SITE, Position
from .reconcile import Result

MAX_RECLAIM_ROWS = 300


def money(v: float) -> str:
    return f"${v:,.0f}"


def write_position_csv(result: Result, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "publisher", "metric", "status", "entitled", "consumed", "installs", "balance",
                    "reclaimable", "balance_after_reclaim", "unit_price", "exposure", "exposure_after_reclaim",
                    "surplus_value", "expired_rights", "renewal_date", "installs_without_usage_data"])
        for p in result.positions:
            w.writerow([p.product, p.publisher, p.metric, p.status, p.entitled, p.consumed, p.installs, p.balance,
                        p.reclaimable, p.balance_after_reclaim, p.unit_price, round(p.exposure, 2),
                        round(p.exposure_after_reclaim, 2), round(p.shelfware, 2), p.expired_rights,
                        p.renewal_date.isoformat() if p.renewal_date else "", p.no_usage_data])


def write_reclaim_csv(result: Result, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "device_name", "device_id", "user", "last_used", "days_unused", "license_value"])
        rows = [c for p in result.positions for c in p.reclaim]
        rows.sort(key=lambda c: (c.product, -c.days_unused))
        for c in rows:
            w.writerow([c.product, c.device_name, c.device_id, c.user, c.last_used.isoformat(), c.days_unused, c.value])


def write_json(result: Result, actions: list[Action], path: Path) -> None:
    path.write_text(json.dumps({
        "tool": "sam-license-reconciler",
        "version": __version__,
        "source": result.source,
        "as_of": result.as_of.isoformat(),
        "devices": result.devices,
        "installs": result.installs,
        "exposure": round(result.exposure, 2),
        "exposure_after_reclaim": round(result.exposure_after_reclaim, 2),
        "reclaimable_installs": result.reclaim_count,
        "reclaim_value": round(result.reclaim_value, 2),
        "surplus_value": round(result.shelfware, 2),
        "products": [
            {"product": p.product, "metric": p.metric, "status": p.status, "entitled": p.entitled,
             "consumed": p.consumed, "balance": p.balance, "balance_after_reclaim": p.balance_after_reclaim,
             "exposure": round(p.exposure, 2), "surplus_value": round(p.shelfware, 2)}
            for p in result.positions
        ],
        "actions": [{"priority": a.priority, "kind": a.kind, "product": a.product, "text": a.text,
                     "amount": round(a.amount, 2)} for a in actions],
        "unrecognized_titles": dict(result.unmatched.most_common()),
    }, indent=2), encoding="utf-8")


def _dollars(p: Position) -> tuple[float, float]:
    """Position in dollars, today and after reclaim: negative is a shortfall,
    positive a surplus. Dollars make products with different metrics comparable."""
    return p.balance * p.unit_price, p.balance_after_reclaim * p.unit_price


def _bar(p: Position, scale: float) -> str:
    """Diverging bar: shortfall grows left in red, surplus grows right in green.
    The solid bar is today; the outline is after reclaim."""
    if p.metric == SITE:
        return '<span class="meta">Unlimited</span>'
    w, mid = 220, 110
    now, after = _dollars(p)

    def span(v):
        length = min(abs(v), scale) / scale * (mid - 4)
        return (mid - length, length) if v < 0 else (mid, length)

    x1, l1 = span(now)
    x2, l2 = span(after)
    c1 = "var(--bad)" if now < 0 else "var(--good)"
    c2 = "var(--bad)" if after < 0 else "var(--good)"
    return (f'<svg class="pos" viewBox="0 0 {w} 26" role="img" aria-label="{money(now)} today, {money(after)} after reclaim">'
            f'<line x1="{mid}" y1="0" x2="{mid}" y2="26" stroke="var(--line-strong)"/>'
            f'<rect x="{x1:.1f}" y="4" width="{max(l1, 1.5):.1f}" height="10" rx="2" fill="{c1}"/>'
            f'<rect x="{x2:.1f}" y="17.5" width="{max(l2, 1.5):.1f}" height="5" rx="1.5" fill="none" stroke="{c2}" stroke-width="1.2"/></svg>')


def write_html(result: Result, actions: list[Action], path: Path) -> None:
    e = escape
    scale = max([abs(v) for p in result.positions if p.metric != SITE for v in _dollars(p)] + [1])

    status_class = {"Shortfall": "bad", "Unlicensed": "bad", "Fixable by reclaim": "warn", "Surplus": "info",
                    "Compliant": "good", "Covered": "good"}

    def num(p: Position, v: int) -> str:
        return f"{v:,}" + (" cores" if p.metric == PER_CORE else "")

    rows = "".join(f"""<tr>
      <td class="prod"><b>{e(p.product)}</b><span class="sub">{e(p.publisher)} · {e(METRIC_LABELS[p.metric])}</span></td>
      <td><span class="pill {status_class[p.status]}">{e(p.status)}</span></td>
      <td class="num">{'Unlimited' if p.metric == SITE else num(p, p.entitled)}</td>
      <td class="num">{num(p, p.consumed)}</td>
      <td class="num">{p.reclaimable or ''}</td>
      <td>{_bar(p, scale)}</td>
      <td class="num {'neg' if p.exposure else ''}">{money(p.exposure) if p.exposure else '—'}</td>
      <td class="num {'neg' if p.exposure_after_reclaim else ''}">{money(p.exposure_after_reclaim) if p.exposure_after_reclaim else '—'}</td>
      <td class="num">{money(p.shelfware) if p.shelfware else '—'}</td>
    </tr>""" for p in result.positions)

    kind_label = {"buy": "Buy", "reclaim": "Reclaim", "renew": "Renewal", "reduce": "Reduce", "reassign": "Reassign",
                  "remove": "Unlicensed", "review": "Review"}
    action_rows = "".join(
        f"""<li><span class="kind k-{a.kind}">{kind_label[a.kind]}</span><p>{e(a.text)}</p>
        <span class="amt">{money(a.amount) if a.amount else ''}</span></li>""" for a in actions)

    reclaim = sorted((c for p in result.positions for c in p.reclaim), key=lambda c: -c.days_unused)
    reclaim_rows = "".join(
        f"""<tr><td>{e(c.product)}</td><td>{e(c.device_name)}</td><td>{e(c.user)}</td>
        <td class="num">{c.last_used:%b %d, %Y}</td><td class="num">{c.days_unused}</td><td class="num">{money(c.value)}</td></tr>"""
        for c in reclaim[:MAX_RECLAIM_ROWS])
    more = (f'<p class="meta">Showing the {MAX_RECLAIM_ROWS} longest-unused of {len(reclaim)}. '
            f'reclaim_candidates.csv has all of them.</p>' if len(reclaim) > MAX_RECLAIM_ROWS else "")

    unmatched = "".join(f"<tr><td>{e(name)}</td><td class='num'>{n}</td></tr>"
                        for name, n in result.unmatched.most_common(15))
    free = ", ".join(f"{e(k)} ({v})" for k, v in result.free_installs.most_common())
    notes = "".join(f"<p class='meta'>Note: {e(n)}</p>" for n in result.notes)
    no_usage = sum(p.no_usage_data for p in result.positions)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>License Position · {e(result.source)}</title>
<style>
:root{{--bg:#f3f5f8;--panel:#fff;--ink:#121826;--soft:#5b6576;--line:#e1e5ec;--line-strong:#b9c1cf;--accent:#2b59c3;
--good:#17865a;--warn:#b97800;--bad:#c52f48;--info:#2b6fb3;
--mono:ui-monospace,"Cascadia Code",Consolas,Menlo,monospace;--sans:"Segoe UI",system-ui,-apple-system,Roboto,sans-serif}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1219;--panel:#161c27;--ink:#e7ebf2;--soft:#9ca6b8;--line:#283041;--line-strong:#46516a;
--accent:#7299ff;--good:#3cc183;--warn:#efa83a;--bad:#ff6b81;--info:#64a8ff;color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);padding:28px 16px 60px}}
.wrap{{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:22px}}
h1{{font-size:26px;margin:0}} h2{{font-size:17px;margin:0 0 12px}}
.eyebrow{{font:600 11px/1 var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}}
.meta,.sub{{color:var(--soft);font-size:13px}} .sub{{display:block;font-size:12px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}}
.kpi{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.kpi b{{display:block;font:700 28px/1.15 var(--sans);font-variant-numeric:tabular-nums;margin-top:6px}}
.kpi span{{font-size:13px;color:var(--soft)}} .kpi .lab{{font:600 11px var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--soft)}}
.kpi.bad b{{color:var(--bad)}} .kpi.good b{{color:var(--good)}}
.tablewrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;font:600 11px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--soft);padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
td{{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:middle}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} td.neg{{color:var(--bad);font-weight:600}}
.pos{{width:160px;height:26px;display:block}} td.prod{{min-width:190px}}
.pill{{display:inline-block;font:600 11.5px var(--sans);padding:3px 9px;border-radius:99px;white-space:nowrap}}
.pill.bad{{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}}
.pill.warn{{background:color-mix(in srgb,var(--warn) 17%,transparent);color:var(--warn)}}
.pill.good{{background:color-mix(in srgb,var(--good) 15%,transparent);color:var(--good)}}
.pill.info{{background:color-mix(in srgb,var(--info) 15%,transparent);color:var(--info)}}
.actions{{list-style:none;margin:0;padding:0;display:grid;gap:10px}}
.actions li{{display:grid;grid-template-columns:96px 1fr auto;gap:14px;align-items:baseline;padding-bottom:10px;border-bottom:1px solid var(--line)}}
.actions li:last-child{{border:0;padding:0}} .actions p{{margin:0}}
.kind{{font:700 10.5px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}}
.k-buy,.k-remove{{color:var(--bad)}} .k-reclaim,.k-renew{{color:var(--warn)}} .k-reduce,.k-reassign{{color:var(--info)}}
.amt{{font:600 14px var(--mono);white-space:nowrap}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--soft);margin:-4px 0 10px}}
.legend i{{display:inline-block;width:18px;height:8px;border-radius:2px;margin-right:6px;vertical-align:1px}}
.two{{display:grid;grid-template-columns:1.4fr 1fr;gap:22px}} .two>*{{min-width:0}}
footer{{color:var(--soft);font-size:12.5px;text-align:center}}
@media (max-width:820px){{.two{{grid-template-columns:1fr}} .actions li{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="eyebrow">Software license position</span>
    <h1>{e(result.source)}</h1>
    <p class="meta">As of {result.as_of:%B %d, %Y} · {result.devices:,} devices · {result.installs:,} installs ·
    report generated {datetime.now():%b %d, %Y %I:%M %p}</p>
  </header>

  <section class="kpis">
    <div class="kpi bad"><span class="lab">Compliance exposure today</span><b>{money(result.exposure)}</b>
      <span>cost to cover every shortfall at current prices</span></div>
    <div class="kpi"><span class="lab">Exposure after reclaim</span><b>{money(result.exposure_after_reclaim)}</b>
      <span>{money(result.exposure - result.exposure_after_reclaim)} avoided by removing unused installs</span></div>
    <div class="kpi good"><span class="lab">Reclaimable</span><b>{result.reclaim_count}</b>
      <span>installs unused for {result.settings.unused_days}+ days, worth {money(result.reclaim_value)}</span></div>
    <div class="kpi"><span class="lab">Surplus rights</span><b>{money(result.shelfware)}</b>
      <span>owned but not needed after reclaim</span></div>
  </section>

  <section class="panel">
    <h2>What to do</h2>
    <ul class="actions">{action_rows or '<li>Nothing to do. Every product is compliant.</li>'}</ul>
  </section>

  <section class="panel">
    <h2>License position by product</h2>
    <div class="legend"><span><i style="background:var(--bad)"></i>Short</span><span><i style="background:var(--good)"></i>Surplus</span>
      <span><i style="border:1.2px solid var(--soft)"></i>After reclaim</span><span>Bars are in dollars so every metric compares</span></div>
    <div class="tablewrap"><table>
      <thead><tr><th>Product</th><th>Status</th><th class="num">Owned</th><th class="num">Needed</th>
      <th class="num">Reclaim</th><th>Position in $</th><th class="num">Exposure</th><th class="num">After reclaim</th><th class="num">Surplus</th></tr></thead>
      <tbody>{rows}</tbody></table></div>
    {notes}
    <p class="meta">"Needed" counts devices, named users or licensable cores depending on the metric. Per-core counts
    apply each product's core factor and per-server minimum. {no_usage} installs had no usage data and were never treated as reclaimable.</p>
  </section>

  <section class="two">
    <div class="panel"><h2>Reclaim candidates ({len(reclaim)})</h2><div class="tablewrap"><table>
      <thead><tr><th>Product</th><th>Device</th><th>User</th><th class="num">Last used</th><th class="num">Days</th><th class="num">Value</th></tr></thead>
      <tbody>{reclaim_rows or '<tr><td colspan="6">None.</td></tr>'}</tbody></table></div>{more}</div>
    <div class="panel"><h2>Not reconciled</h2>
      <p class="meta" style="margin-top:0">Titles no rule recognized. Add a rule for the commercial ones.</p>
      <div class="tablewrap"><table><thead><tr><th>Display name</th><th class="num">Installs</th></tr></thead>
      <tbody>{unmatched or '<tr><td colspan="2">Everything was recognized.</td></tr>'}</tbody></table></div>
      <p class="meta">Free software, tracked but not licensed: {free or 'none found'}.</p></div>
  </section>

  <footer>sam-license-reconciler v{__version__} · read-only analysis · prices come from your entitlements or catalog</footer>
</div>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")


def write_all(result: Result, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    actions = build_actions(result)
    paths = {
        "html": out_dir / "license_position.html",
        "positions": out_dir / "license_position.csv",
        "reclaim": out_dir / "reclaim_candidates.csv",
        "json": out_dir / "license_summary.json",
    }
    write_html(result, actions, paths["html"])
    write_position_csv(result, paths["positions"])
    write_reclaim_csv(result, paths["reclaim"])
    write_json(result, actions, paths["json"])
    return paths

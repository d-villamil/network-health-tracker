#!/usr/bin/env python3
"""
Publish scorecard to GitHub Pages.
Fetches current data from the localhost dashboard API and generates a static HTML file,
then pushes to the gh-pages branch.

Usage:
  python publish_scorecard.py           # fetch from localhost + push
  python publish_scorecard.py --dry-run # generate HTML only, don't push
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("publish")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

ET = ZoneInfo("America/New_York")
DASHBOARD_URL = "http://127.0.0.1:5000"
GH_PAGES_DIR = Path(__file__).parent / "gh-pages"
REPO_URL = "https://github.com/d-villamil/network-health-tracker.git"


def fetch_scorecard_data(force=False) -> dict | None:
    """Fetch scorecard data from the running localhost dashboard."""
    # Only do full refresh during ops hours (5am-2pm ET) for scheduled runs
    now_et = datetime.now(ET)
    if now_et.hour >= 14 and not force:
        log.info(f"After ops hours ({now_et.strftime('%-I:%M %p ET')}) — skipping publish")
        return None

    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/scorecard/refresh", timeout=600)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Failed to fetch from dashboard: {e}")
        return None


def fetch_tracked_actions() -> dict:
    """Read today's tracked actions from Google Sheet. Returns {site: [{action, notes, timestamp}]}."""
    try:
        import gspread
        gc = gspread.oauth(
            credentials_filename=str(Path(__file__).parent / "credentials.json"),
            authorized_user_filename=str(Path(__file__).parent / "token.json"),
        )
        ss = gc.open_by_key("1cQH7gwBvAmZO8WiYNPbrCSnO8zxB0DUDhzaeMM5o-ZU")
        ws = ss.worksheet("Tracked Actions")
        all_rows = ws.get_all_values()
        if len(all_rows) <= 1:
            return {}

        today_str = date.today().strftime("%-m/%-d/%Y")
        today_str2 = date.today().strftime("%Y-%m-%d")
        tracked = {}
        for row in all_rows[1:]:
            if len(row) < 6:
                continue
            ts = row[0]
            if today_str not in ts and today_str2 not in ts and str(date.today()) not in ts:
                continue
            site = row[1]
            if not site:
                continue
            tracked.setdefault(site, []).append({
                "timestamp": ts,
                "action": row[4] if len(row) > 4 else "",
                "user": row[5] if len(row) > 5 else "",
            })
        log.info(f"Tracked actions from sheet: {sum(len(v) for v in tracked.values())} entries across {len(tracked)} sites")
        return tracked
    except Exception as e:
        log.warning(f"Could not read tracked actions: {e}")
        return {}


def generate_html(data: dict, tracked_actions: dict = None) -> str:
    """Generate a self-contained static HTML scorecard page."""
    rows = data.get("rows", [])
    timeline = data.get("timeline", {})
    tracked_actions = tracked_actions or {}
    last_updated = data.get("last_updated", "Unknown")
    now = datetime.now(ET)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏥</text></svg>">
    <title>📦 Network Health Scorecard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #2a3d50; color: #e0e6ed; font-size: 14px; }}

        header {{ display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; background: #334d63; border-bottom: 1px solid #456a85; }}
        h1 {{ font-size: 20px; font-weight: 600; }}
        h1 a {{ color: inherit; text-decoration: none; }}
        h1 a span.link-hint {{ font-size: 11px; color: #4fc3f7; margin-left: 8px; vertical-align: middle; }}
        .meta {{ font-size: 12px; color: #99aabb; margin-left: 12px; }}
        .published {{ font-size: 11px; color: #99aabb; padding: 8px 24px; background: #263849; text-align: center; }}

        main {{ padding: 16px 24px; }}

        .section {{ margin-bottom: 32px; }}
        .section-title {{ font-size: 16px; font-weight: 600; color: #e0e6ed; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 2px solid #456a85; }}
        .section-count {{ font-size: 13px; font-weight: 400; color: #99aabb; }}

        table {{ width: 100%; border-collapse: collapse; background: #334d63; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }}
        thead {{ background: #263849; position: sticky; top: 0; z-index: 10; }}
        th {{ padding: 10px 12px; text-align: left; font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; color: #b0c0d0; cursor: pointer; user-select: none; }}
        th:hover {{ background: #456a85; }}
        th.sorted-asc::after {{ content: " \\25B2"; font-size: 10px; }}
        th.sorted-desc::after {{ content: " \\25BC"; font-size: 10px; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #456a85; font-variant-numeric: tabular-nums; color: #dce4ec; }}
        tbody tr:nth-child(even) {{ background: #385268; }}
        tbody tr:nth-child(odd) {{ background: #334d63; }}
        tbody tr:hover {{ background: #426280 !important; }}

        .th-sub {{ font-size: 10px; font-weight: 400; color: #7799aa; text-transform: none; letter-spacing: 0; }}
        .site-dropdown {{ position:absolute; top:100%%; left:0; width:100%%; max-height:200px; overflow-y:auto; background:#334d63; border:1px solid #456a85; border-radius:6px; z-index:50; display:none; }}
        .site-dropdown.open {{ display:block; }}
        .site-dropdown-item {{ padding:6px 12px; cursor:pointer; font-size:13px; color:#dce4ec; }}
        .site-dropdown-item:hover {{ background:#456a85; }}

        [data-tip] {{ position: relative; cursor: default; }}
        [data-tip]:hover::after {{
            content: attr(data-tip);
            position: absolute; bottom: 100%%; left: 50%%; transform: translateX(-50%%);
            background: #1a2a3a; color: #dce4ec; padding: 4px 8px; border-radius: 4px;
            font-size: 11px; white-space: nowrap; z-index: 99; pointer-events: none;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }}

        .status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }}
        .dot-open {{ background: #4caf50; box-shadow: 0 0 4px #4caf50; }}
        .dot-closed {{ background: #636e72; }}
        .cell-site {{ min-width: 90px; white-space: nowrap; }}
        .cell-site a {{ color: #4fc3f7; text-decoration: none; }}
        .cell-site a:hover {{ text-decoration: underline; }}
        .cet-cell {{ white-space: normal !important; min-width: 150px; }}
        .cell-green-dark {{ background: #265a38; color: #81c784; font-weight: 600; }}
        .cell-red-dark {{ background: #5a2626; color: #ef5350; font-weight: 600; }}
        .cell-yellow-dark {{ background: #5a5a26; color: #ffca28; font-weight: 600; }}
        .no-data {{ color: #556677; }}

        .cet-cell {{ white-space: nowrap; }}
        .cet-truck {{ display: inline-block; margin-right: 10px; font-size: 13px; color: #dce4ec; }}
        .cet-late-detail {{ font-size: 11px; color: #ef5350; font-weight: 500; }}
        .cet-miss-link {{ text-decoration: none; color: #ef5350; }}
        .cet-miss-link:hover {{ text-decoration: underline; }}
        .cet-na {{ color: #556677; font-size: 12px; }}

        .dispatch-dot {{ display: inline-block; width: 8px; height: 8px; background: #4caf50; border-radius: 50%; margin-right: 6px; vertical-align: middle; box-shadow: 0 0 4px #4caf50; }}
        .dispatch-dot-yellow {{ display: inline-block; width: 8px; height: 8px; background: #ffc107; border-radius: 50%; margin-right: 6px; vertical-align: middle; box-shadow: 0 0 4px #ffc107; }}
        .alert-badge {{ display: inline-block; background: #ef5350; color: #fff; font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 8px; margin-left: 4px; vertical-align: middle; }}

        .flag-tag {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; text-decoration: none; margin-right: 4px; }}
        .flag-tag:hover {{ opacity: 0.8; text-decoration: underline; }}
        .flag-ap {{ background: #5a2626; color: #ef5350; }}
        .flag-network {{ background: #5a5a26; color: #ffca28; }}
        .flag-transport {{ background: #263849; color: #4fc3f7; }}
        .flag-tracker {{ background: #265a38; color: #81c784; }}

        .region-header td {{ background: #263849 !important; color: #4fc3f7; font-weight: 700; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; padding: 10px 12px; border-bottom: 2px solid #456a85 !important; }}
        .region-count {{ font-weight: 400; font-size: 11px; color: #99aabb; text-transform: none; letter-spacing: 0; }}

        tr.row-red {{ border-left: 4px solid #ef5350; }}
        tr.row-yellow {{ border-left: 4px solid #ffc107; }}
        tr.row-tracked {{ border-left: 4px solid #4caf50; opacity: 0.6; }}
        tr.row-tracked:hover {{ opacity: 0.8; }}
        .tracked-info {{ font-size: 11px; color: #4caf50; }}
        .tracked-info a {{ color: #4caf50; text-decoration: none; }}
        .tracked-info a:hover {{ text-decoration: underline; }}
        .region-col-header th {{
            background: #2a3d50 !important;
            color: #99aabb;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 6px 12px;
            border-bottom: 1px solid #456a85 !important;
            font-weight: 500;
        }}

        tr.expandable {{ cursor: pointer; }}
        .timeline-row-dark {{ background: #263849 !important; }}
        .timeline-row-dark td {{ border-bottom-color: #2a3a4e !important; }}
        .timeline-cell {{ padding: 4px 12px !important; }}
        .timeline-time {{ color: #99aabb; font-size: 12px; margin-right: 10px; }}
        .timeline-label {{ color: #dce4ec; font-size: 12px; }}

        select {{ font-size: 13px; padding: 6px 12px; border-radius: 6px; border: 1px solid #456a85; background: #334d63; color: #dce4ec; cursor: pointer; }}
        .btn-track-static {{ padding: 4px 12px; border-radius: 4px; border: 1px solid #4fc3f7; background: transparent; color: #4fc3f7; font-size: 12px; font-weight: 500; cursor: pointer; }}
        .btn-track-static:hover {{ background: #4fc3f7; color: #1a2332; }}
        .modal-overlay {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 100; }}
        .modal-overlay.hidden {{ display: none; }}
        .modal {{ background: #334d63; border-radius: 12px; padding: 24px; width: 440px; max-width: 90vw; box-shadow: 0 8px 32px rgba(0,0,0,0.3); color: #dce4ec; }}
        .modal h2 {{ font-size: 16px; margin-bottom: 12px; }}
        .modal-info {{ display: flex; gap: 16px; margin-bottom: 16px; padding: 8px 12px; background: #2a3d50; border-radius: 6px; font-size: 13px; flex-wrap: wrap; }}
        .modal label {{ display: block; font-size: 12px; font-weight: 600; color: #99aabb; margin: 10px 0 4px; text-transform: uppercase; letter-spacing: 0.3px; }}
        .modal input, .modal select, .modal textarea {{ width: 100%; padding: 8px 10px; border: 1px solid #456a85; border-radius: 6px; font-size: 14px; font-family: inherit; background: #2a3d50; color: #dce4ec; }}
        .modal textarea {{ resize: vertical; }}
        .modal-actions {{ display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }}
        .btn-cancel {{ padding: 6px 12px; border-radius: 6px; border: 1px solid #456a85; background: transparent; color: #99aabb; cursor: pointer; font-size: 13px; }}
        .btn-submit {{ padding: 6px 12px; border-radius: 6px; border: none; background: #00b894; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px; }}
        .btn-submit:hover {{ background: #00a381; }}
        .btn-submit:disabled {{ background: #556677; cursor: wait; }}

        .tabs {{ display: flex; gap: 0; padding: 0 24px; background: #334d63; border-bottom: 1px solid #456a85; }}
        .tab {{ padding: 10px 18px; color: #99aabb; text-decoration: none; font-size: 14px; border-bottom: 2px solid transparent; }}
        .tab:hover {{ color: #e0e6ed; }}
        .tab.active {{ color: #4fc3f7; border-bottom-color: #4fc3f7; }}
    </style>
</head>
<body>
    <header>
        <div>
            <h1><a href="https://docs.google.com/spreadsheets/d/1cQH7gwBvAmZO8WiYNPbrCSnO8zxB0DUDhzaeMM5o-ZU/edit?gid=11136630#gid=11136630" target="_blank">Network Health Scorecard <span class="link-hint">📋 Open Tracker</span></a></h1>
            <span class="meta">Last updated: {last_updated}</span>
        </div>
        <select id="dispatch-filter" onchange="renderTable()" style="margin-right:8px;">
            <option value="all">All Dispatch</option>
            <option value="on">Dispatch On</option>
            <option value="off">Dispatch Off</option>
        </select>
        <div class="site-search-wrap" style="margin-right:8px;position:relative;display:inline-block;">
            <input type="text" id="site-search" placeholder="Search site..." autocomplete="off"
                   style="width:120px;padding:6px 12px;border-radius:6px;border:1px solid #456a85;background:#334d63;color:#dce4ec;font-size:13px;">
            <div id="site-dropdown" class="site-dropdown"></div>
        </div>
        <select id="pod-filter" onchange="renderTable()">
            <option value="all">All Regions</option>
            <option value="Northeast">Northeast</option>
            <option value="Southeast">Southeast</option>
            <option value="Central">Central</option>
            <option value="West">West</option>
        </select>
    </header>
    <nav class="tabs">
        <a href="index.html" class="tab active">Scorecard</a>
        <a href="hubs.html" class="tab">Hubs</a>
    </nav>
    <div class="published">Published {now.strftime("%-I:%M %p ET, %B %d")} — auto-refreshes every 15 min during ops hours</div>

    <main>
        <section class="section">
            <h2 class="section-title">Spoke Health Scorecard <span id="scorecard-count" class="section-count"></span></h2>
            <table id="scorecard-table">
                <thead>
                    <tr>
                        <th data-col="site">Site</th>
                        <th data-col="pod">Region</th>
                        <th data-col="cet_status">CET Met</th>
                        <th data-col="small_batches">Small Batches</th>
                        <th data-col="scan_start">Sort Scan Start<br><span class="th-sub">local time</span></th>
                        <th data-col="dispatch_start">Dispatch Start<br><span class="th-sub">local time</span></th>
                        <th data-col="needs_replan">Needs Replan</th>
                        <th data-col="return_bin">Return Bin</th>
                        <th data-col="lfr_over_45">LFR > 45m</th>
                        <th data-col="plib">PLIB</th>
                        <th>Flags</th>
                    </tr>
                </thead>
                <tbody id="scorecard-body"></tbody>
            </table>
        </section>
    </main>

    <div id="modal-overlay" class="modal-overlay hidden" onclick="if(event.target===this)hideModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h2>Track Action — <span id="modal-site"></span></h2>
            <div class="modal-info" id="modal-info"></div>
            <form onsubmit="submitTrack(event)">
                <input type="hidden" id="track-site">
                <input type="hidden" id="track-pod">
                <input type="hidden" id="track-replan">
                <input type="hidden" id="track-sb">
                <input type="hidden" id="track-rb">
                <input type="hidden" id="track-plib">
                <label>Analyst Name</label>
                <input type="text" id="track-analyst" required placeholder="Your name">
                <label>Exception Type</label>
                <select id="track-exception" required onchange="updateQuantity()">
                    <option value="">Select exception...</option>
                    <option value="Needs Replan">Needs Replan</option>
                    <option value="Small Batch">Small Batch</option>
                    <option value="Return Bin">Return Bin</option>
                    <option value="PLIB">PLIB</option>
                    <option value="Dispatch">Dispatch</option>
                    <option value="Operational Issue">Operational Issue</option>
                </select>
                <label>Quantity</label>
                <input type="text" id="track-quantity" readonly>
                <label>Notes</label>
                <textarea id="track-notes" rows="3" placeholder="What did you do? How did it impact the value?"></textarea>
                <div class="modal-actions">
                    <button type="button" class="btn-cancel" onclick="hideModal()">Cancel</button>
                    <button type="submit" class="btn-submit" id="submit-btn">Track It</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        const WEBHOOK_URL = 'https://script.google.com/a/macros/doordash.com/s/AKfycbw4vbemVuziIiTAXgVbvOFTJ_huWhk9L4d3dQdYaH5DydbH1Um0X0ubJEU4hP___y4grg/exec';
        const DATA = {json.dumps(rows)};
        const TRACKED = {json.dumps(tracked_actions)};
        const TIMELINE = {json.dumps(timeline)};
        let sortCol = 'site';
        let sortAsc = true;
        const expandedSites = new Set();

        function buildFlagsCell(flags) {{
            if (!flags || flags.length === 0) return '';
            const grouped = {{}};
            flags.forEach(f => {{
                if (!grouped[f.category]) grouped[f.category] = [];
                grouped[f.category].push(f);
            }});
            return Object.entries(grouped).map(([cat, items]) => {{
                const count = items.length > 1 ? ` (${{items.length}})` : '';
                const link = items[0].link;
                const previews = items.map(i => i.preview).join('\\n');
                return `<a href="${{link}}" target="_blank" class="flag-tag flag-${{cat.toLowerCase()}}" title="${{previews}}">${{cat}}${{count}}</a>`;
            }}).join(' ');
        }}

        function renderTable() {{
            const filter = document.getElementById('pod-filter').value;
            const dispatchFilter = document.getElementById('dispatch-filter').value;
            let rows = DATA;
            if (selectedSite !== 'all') {{
                rows = rows.filter(r => r.site === selectedSite);
            }} else if (filter !== 'all') {{
                rows = rows.filter(r => r.pod === filter);
            }}
            if (dispatchFilter === 'on') {{
                rows = rows.filter(r => r.dispatch_active);
            }} else if (dispatchFilter === 'off') {{
                rows = rows.filter(r => !r.dispatch_active);
            }}

            rows = [...rows].sort((a, b) => {{
                let va, vb;
                if (sortCol === 'cet_status') {{
                    va = a.cet_total > 0 ? a.cet_met / a.cet_total : 1;
                    vb = b.cet_total > 0 ? b.cet_met / b.cet_total : 1;
                }} else {{
                    va = a[sortCol] || ''; vb = b[sortCol] || '';
                    if (typeof va === 'string') va = va.toLowerCase();
                    if (typeof vb === 'string') vb = vb.toLowerCase();
                }}
                if (va < vb) return sortAsc ? -1 : 1;
                if (va > vb) return sortAsc ? 1 : -1;
                return 0;
            }});

            document.getElementById('scorecard-count').textContent = `(${{rows.length}} spokes)`;
            const tbody = document.getElementById('scorecard-body');
            tbody.innerHTML = '';

            const regions = ['Northeast', 'Southeast', 'Central', 'West'];
            const grouped = {{}};
            regions.forEach(reg => {{ grouped[reg] = []; }});
            rows.forEach(r => {{
                const reg = r.pod || 'Other';
                if (!grouped[reg]) grouped[reg] = [];
                grouped[reg].push(r);
            }});

            regions.forEach(region => {{
                const regionRows = grouped[region] || [];
                if (regionRows.length === 0) return;
                const headerTr = document.createElement('tr');
                headerTr.className = 'region-header';
                headerTr.innerHTML = `<td colspan="11">${{region}} <span class="region-count">(${{regionRows.length}} sites)</span></td>`;
                tbody.appendChild(headerTr);

                const colTr = document.createElement('tr');
                colTr.className = 'region-col-header';
                colTr.innerHTML = `
                    <th>Site</th><th>Region</th><th>CET Met</th><th>Small Batches</th>
                    <th>Sort Scan Start</th><th>Dispatch Start</th><th>Needs Replan</th>
                    <th>Return Bin</th><th>LFR > 45m</th><th>PLIB</th><th>Flags</th>`;
                tbody.appendChild(colTr);

                regionRows.forEach(r => {{
                    const tr = document.createElement('tr');
                    const siteTimeline = TIMELINE[r.site];
                    const hasAlerts = siteTimeline && siteTimeline.events && siteTimeline.events.length > 0;
                    const isExpanded = expandedSites.has(r.site);

                    const isTracked = TRACKED[r.site] && TRACKED[r.site].length > 0;
                    tr.classList.add('expandable');
                    if (isTracked) tr.classList.add('row-tracked');
                    if (isExpanded) tr.classList.add('expanded');
                    tr.addEventListener('click', (e) => {{
                        if (e.target.tagName === 'A') return;
                        if (expandedSites.has(r.site)) {{ expandedSites.delete(r.site); }}
                        else {{ expandedSites.add(r.site); }}
                        renderTable();
                    }});

                    if (r.cet_total > 0) {{
                        const pct = r.cet_met / r.cet_total;
                        if (pct === 0) tr.classList.add('row-red');
                        else if (pct < 1) tr.classList.add('row-yellow');
                    }}

                    let cetCell = '';
                    if (r.cet_total === 0) {{
                        cetCell = '<span class="cet-na">No inbound</span>';
                    }} else {{
                        const trucks = r.cet_trucks || [];
                        cetCell = trucks.map(t => {{
                            const icon = t.met ? '✅' : (t.status === 'pending_late' ? '⏳' : '❌');
                            let label = `${{t.origin}} ${{icon}}`;
                            if (!t.met && t.minutes_late > 0) label += ` <span class="cet-late-detail">${{t.minutes_late}}m late</span>`;
                            if (!t.met && t.status === 'pending_late') label += ` <span class="cet-late-detail">pending</span>`;
                            if (!t.met && t.arrival_reason) label += ` <span class="cet-late-detail">(${{t.arrival_reason}})</span>`;
                            if (!t.met && t.shipment_id) return `<a href="https://parcels.doordash.com/network/shipments/${{t.shipment_id}}" target="_blank" class="cet-truck cet-miss-link" title="${{t.origin}} → CET: ${{t.cet}}, Actual: ${{t.actual || 'pending'}}, Carrier: ${{t.carrier}}">${{label}}</a>`;
                            return `<span class="cet-truck" title="${{t.origin}} → CET: ${{t.cet}}, Actual: ${{t.actual || 'pending'}}, Carrier: ${{t.carrier}}">${{label}}</span>`;
                        }}).join(' ');
                    }}

                    const scanClass = r.scan_start ? (r.scan_start_diff > 15 ? 'cell-red-dark' : r.scan_start_avg ? 'cell-green-dark' : '') : '';
                    const scanTitle = r.scan_start_avg ? `Avg: ${{r.scan_start_avg}}, diff: ${{r.scan_start_diff > 0 ? '+' : ''}}${{r.scan_start_diff}} min` : '';
                    const dispatchClass = r.dispatch_start ? (r.dispatch_diff > 15 ? 'cell-red-dark' : r.dispatch_start_avg ? 'cell-green-dark' : '') : '';
                    const dispatchTitle = r.dispatch_start_avg ? `Avg: ${{r.dispatch_start_avg}}, diff: ${{r.dispatch_diff > 0 ? '+' : ''}}${{r.dispatch_diff}} min` : '';
                    const sbClass = r.small_batches > 5 ? 'cell-yellow-dark' : '';
                    const expandIcon = isExpanded ? '▾ ' : '▸ ';
                    const alertBadge = hasAlerts ? `<span class="alert-badge">${{siteTimeline.events.length}}</span>` : '';

                    tr.innerHTML = `
                        <td class="cell-site">${{expandIcon}}<span class="status-dot ${{r.site_open ? 'dot-open' : 'dot-closed'}}"></span><a href="https://parcels.doordash.com/exceptions?facility_code=${{r.site}}" target="_blank">${{r.site}}</a> ${{alertBadge}}</td>
                        <td>${{r.pod}}</td>
                        <td class="cet-cell">${{cetCell}}</td>
                        <td class="${{sbClass}}">${{r.small_batches || '<span class="no-data">0</span>'}}</td>
                        <td class="${{scanClass}}" data-tip="${{scanTitle}}">${{r.scan_start || '<span class="no-data">—</span>'}}</td>
                        <td class="${{dispatchClass}}" data-tip="${{dispatchTitle}}">${{r.dispatch_toggle ? '<span class="dispatch-dot"></span>' : r.has_active_runners ? '<span class="dispatch-dot-yellow"></span>' : ''}}${{r.dispatch_start || '<span class="no-data">—</span>'}}</td>
                        <td class="${{r.needs_replan >= 35 ? 'cell-red-dark' : r.needs_replan >= 15 ? 'cell-yellow-dark' : ''}}">${{r.needs_replan || '<span class="no-data">0</span>'}}</td>
                        <td class="${{r.return_bin >= 15 ? 'cell-red-dark' : r.return_bin >= 5 ? 'cell-yellow-dark' : ''}}">${{r.return_bin || '<span class="no-data">0</span>'}}</td>
                        <td class="${{r.lfr_over_45 > 0 ? 'cell-yellow-dark' : ''}}">${{r.lfr_over_45 || '<span class="no-data">0</span>'}}</td>
                        <td class="${{r.plib >= 30 ? 'cell-red-dark' : r.plib >= 15 ? 'cell-yellow-dark' : ''}}">${{r.plib || '<span class="no-data">0</span>'}}</td>
                        <td class="flags-cell">${{buildFlagsCell(r.flags || [])}}</td>
                    `;
                    tbody.appendChild(tr);

                    // Expanded section
                    if (isExpanded) {{
                        if (hasAlerts) {{
                            const events = [...siteTimeline.events].reverse();
                            events.forEach(evt => {{
                                const subTr = document.createElement('tr');
                                subTr.className = 'timeline-row-dark';
                                subTr.innerHTML = `
                                    <td colspan="2"></td>
                                    <td colspan="9" class="timeline-cell">
                                        <span class="timeline-time">${{evt.time}}</span>
                                        <span class="timeline-label">${{evt.label}}</span>
                                    </td>
                                `;
                                tbody.appendChild(subTr);
                            }});
                        }}
                        // Show tracked actions if any
                        if (isTracked) {{
                            TRACKED[r.site].forEach(t => {{
                                const userName = t.user ? t.user.split('@')[0].replace('.', ' ') : '';
                                const tTr = document.createElement('tr');
                                tTr.className = 'timeline-row-dark';
                                tTr.innerHTML = `
                                    <td colspan="2"></td>
                                    <td colspan="9" class="timeline-cell">
                                        <span class="tracked-info">\u2705 ${{userName}} — ${{t.action}}</span>
                                    </td>
                                `;
                                tbody.appendChild(tTr);
                            }});
                        }}

                        const trackTr = document.createElement('tr');
                        trackTr.className = 'timeline-row-dark';
                        trackTr.innerHTML = `
                            <td colspan="2"></td>
                            <td colspan="9" class="timeline-cell">
                                <button class="btn-track-static"
                                        onclick="event.stopPropagation(); openTrack('${{r.site}}', '${{r.pod}}', ${{r.needs_replan || 0}}, ${{r.small_batches || 0}}, ${{r.return_bin || 0}}, ${{r.plib || 0}})">
                                    Track It
                                </button>
                            </td>
                        `;
                        tbody.appendChild(trackTr);
                    }}
                }});
            }});

            document.querySelectorAll('th').forEach(th => {{
                th.classList.remove('sorted-asc', 'sorted-desc');
                if (th.dataset.col === sortCol) th.classList.add(sortAsc ? 'sorted-asc' : 'sorted-desc');
            }});
        }}

        document.querySelectorAll('th').forEach(th => {{
            th.addEventListener('click', () => {{
                const col = th.dataset.col;
                if (!col) return;
                if (sortCol === col) {{ sortAsc = !sortAsc; }}
                else {{ sortCol = col; sortAsc = (col === 'site' || col === 'pod'); }}
                renderTable();
            }});
        }});

        // Track It modal
        const savedAnalyst = localStorage.getItem('dashboard_analyst') || '';
        document.getElementById('track-analyst').value = savedAnalyst;

        function openTrack(site, pod, replan, sb, rb, plib) {{
            document.getElementById('modal-site').textContent = site;
            document.getElementById('modal-info').innerHTML =
                `<span>Needs Replan: <strong>${{replan}}</strong></span>
                 <span>Small Batches: <strong>${{sb}}</strong></span>
                 <span>Return Bin: <strong>${{rb}}</strong></span>
                 <span>PLIB: <strong>${{plib}}</strong></span>`;
            document.getElementById('track-site').value = site;
            document.getElementById('track-pod').value = pod;
            document.getElementById('track-replan').value = replan;
            document.getElementById('track-sb').value = sb;
            document.getElementById('track-rb').value = rb;
            document.getElementById('track-plib').value = plib;
            document.getElementById('track-exception').value = '';
            document.getElementById('track-quantity').value = '';
            document.getElementById('track-notes').value = '';
            document.getElementById('track-analyst').value = localStorage.getItem('dashboard_analyst') || '';
            document.getElementById('modal-overlay').classList.remove('hidden');
        }}

        function updateQuantity() {{
            const type = document.getElementById('track-exception').value;
            const map = {{
                'Needs Replan': document.getElementById('track-replan').value,
                'Small Batch': document.getElementById('track-sb').value,
                'Return Bin': document.getElementById('track-rb').value,
                'PLIB': document.getElementById('track-plib').value,
                'Dispatch': '0',
            }};
            document.getElementById('track-quantity').value = map[type] || '';
        }}

        function hideModal() {{ document.getElementById('modal-overlay').classList.add('hidden'); }}

        function submitTrack(event) {{
            event.preventDefault();
            const analyst = document.getElementById('track-analyst').value;
            localStorage.setItem('dashboard_analyst', analyst);
            const now = new Date().toLocaleString('en-US', {{timeZone: 'America/New_York'}});
            const params = new URLSearchParams({{
                timestamp: now,
                site: document.getElementById('track-site').value,
                region: document.getElementById('track-pod').value,
                quantity: document.getElementById('track-quantity').value || '0',
                action: document.getElementById('track-exception').value,
                analyst: analyst,
                notes: document.getElementById('track-notes').value,
            }});
            window.open(WEBHOOK_URL + '?' + params.toString(), '_blank');
            hideModal();
        }}

        // Site search
        const allSites = DATA.map(r => r.site).sort();
        let selectedSite = 'all';
        const siteInput = document.getElementById('site-search');
        const siteDropdownEl = document.getElementById('site-dropdown');

        function showSiteDropdown(filter) {{
            const q = (filter || '').toUpperCase();
            const matches = q ? allSites.filter(s => s.includes(q)) : allSites;
            siteDropdownEl.innerHTML = '<div class="site-dropdown-item" data-val="all">All Sites</div>' +
                matches.map(s => `<div class="site-dropdown-item" data-val="${{s}}">${{s}}</div>`).join('');
            siteDropdownEl.classList.add('open');
        }}

        siteInput.addEventListener('focus', () => showSiteDropdown(siteInput.value));
        siteInput.addEventListener('input', () => showSiteDropdown(siteInput.value));
        siteDropdownEl.addEventListener('click', (e) => {{
            const item = e.target.closest('.site-dropdown-item');
            if (!item) return;
            selectedSite = item.dataset.val;
            siteInput.value = selectedSite === 'all' ? '' : selectedSite;
            siteDropdownEl.classList.remove('open');
            renderTable();
        }});
        document.addEventListener('click', (e) => {{
            if (!e.target.closest('.site-search-wrap')) siteDropdownEl.classList.remove('open');
        }});

        renderTable();
    </script>
</body>
</html>"""


def fetch_hubs_data(force=False) -> dict | None:
    """Fetch hubs/outbound-kiosk data from the running localhost dashboard."""
    now_et = datetime.now(ET)
    if now_et.hour >= 14 and not force:
        return None
    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/hubs/refresh", timeout=600)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Failed to fetch hubs from dashboard: {e}")
        return None


def generate_hubs_html(data: dict) -> str:
    """Generate static hubs page mirroring the localhost /hubs view."""
    hubs = data.get("hubs", {})
    hub_codes = data.get("hub_codes", [])
    last_updated = data.get("last_updated", "Unknown")
    now = datetime.now(ET)

    hubs_json = json.dumps(hubs)
    codes_json = json.dumps(hub_codes)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚚</text></svg>">
    <title>📦 Network Hubs</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #2a3d50; color: #e0e6ed; font-size: 14px; }}

        header {{ display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; background: #334d63; border-bottom: 1px solid #456a85; }}
        h1 {{ font-size: 20px; font-weight: 600; }}
        h1 a {{ color: inherit; text-decoration: none; }}
        .meta {{ font-size: 12px; color: #99aabb; margin-left: 12px; }}
        .published {{ font-size: 11px; color: #99aabb; padding: 8px 24px; background: #263849; text-align: center; }}

        .tabs {{ display: flex; gap: 0; padding: 0 24px; background: #334d63; border-bottom: 1px solid #456a85; }}
        .tab {{ padding: 10px 18px; color: #99aabb; text-decoration: none; font-size: 14px; border-bottom: 2px solid transparent; }}
        .tab:hover {{ color: #e0e6ed; }}
        .tab.active {{ color: #4fc3f7; border-bottom-color: #4fc3f7; }}

        .direction-toggle {{ display: flex; gap: 4px; padding: 12px 24px 0; }}
        .dir-btn {{ padding: 8px 18px; background: #334d63; color: #99aabb; border: 1px solid #456a85; border-radius: 6px 6px 0 0; cursor: pointer; font-size: 14px; font-weight: 500; }}
        .dir-btn:hover {{ color: #e0e6ed; }}
        .dir-btn.active {{ background: #263849; color: #4fc3f7; border-bottom-color: #263849; }}

        main {{ padding: 16px 24px; }}

        .hub-jump {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; padding: 12px; background: #334d63; border-radius: 8px; }}
        .hub-jump a {{ padding: 6px 12px; background: #263849; color: #4fc3f7; text-decoration: none; border-radius: 4px; font-size: 13px; font-weight: 500; }}
        .hub-jump a:hover {{ background: #456a85; color: #fff; }}
        .hub-jump .count {{ color: #99aabb; font-size: 11px; margin-left: 4px; }}

        .hub-section {{ margin-bottom: 28px; background: #334d63; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.3); overflow: hidden; }}
        .hub-header {{ padding: 12px 16px; background: #263849; border-bottom: 2px solid #456a85; display: flex; justify-content: space-between; align-items: center; }}
        .hub-name {{ font-size: 16px; font-weight: 700; color: #4fc3f7; letter-spacing: 0.5px; }}
        .hub-stats {{ font-size: 12px; color: #99aabb; }}
        .hub-stats .stat-pill {{ display: inline-block; padding: 2px 8px; margin-left: 6px; border-radius: 10px; background: #2a3d50; color: #dce4ec; font-weight: 500; }}
        .hub-stats .stat-late {{ background: #5a2626; color: #ef5350; }}
        .hub-stats .stat-empty {{ background: #5a5a26; color: #ffca28; }}
        .hub-stats .stat-active {{ background: #1a4068; color: #4fc3f7; }}
        .hub-stats .stat-done {{ background: #265a38; color: #81c784; }}

        table {{ width: 100%; border-collapse: collapse; }}
        thead {{ background: #2a3d50; }}
        th {{ padding: 8px 12px; text-align: left; font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #b0c0d0; white-space: nowrap; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #456a85; font-variant-numeric: tabular-nums; color: #dce4ec; font-size: 13px; }}
        tbody tr:nth-child(even) {{ background: #385268; }}
        tbody tr:nth-child(odd) {{ background: #334d63; }}
        tbody tr:hover {{ background: #426280; }}

        tr.row-late {{ border-left: 4px solid #ef5350; }}
        tr.row-empty {{ border-left: 4px solid #ffc107; }}
        tr.row-departed td {{ color: #99aabb; }}
        tr.row-not-started {{ border-left: 4px solid #ffc107; }}
        tr.row-done td {{ color: #81c784; }}

        .late-badge {{ display: inline-block; background: #5a2626; color: #ef5350; font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 8px; margin-left: 6px; }}
        .empty-badge {{ display: inline-block; background: #5a5a26; color: #ffca28; font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 8px; margin-left: 6px; }}
        .status-departed {{ color: #81c784; font-weight: 500; }}
        .status-scheduled {{ color: #ffca28; font-weight: 500; }}
        .status-inducting {{ color: #4fc3f7; font-weight: 500; }}
        .status-not-started {{ color: #ffca28; font-weight: 500; }}
        .status-done {{ color: #81c784; font-weight: 500; }}
        .no-data {{ color: #556677; font-style: italic; padding: 16px; text-align: center; }}
        .dest-cell {{ font-weight: 700; color: #4fc3f7; }}
        .ship-link {{ color: #99aabb; font-size: 11px; text-decoration: none; }}
        .ship-link:hover {{ color: #4fc3f7; text-decoration: underline; }}
        .origin-cell {{ font-size: 12px; color: #dce4ec; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

        .progress-bar {{ display: inline-block; width: 100px; height: 12px; background: #2a3d50; border-radius: 6px; overflow: hidden; vertical-align: middle; margin-right: 8px; }}
        .progress-fill {{ height: 100%; background: #4fc3f7; transition: width 0.3s; }}
        .progress-fill.done {{ background: #81c784; }}
        .progress-fill.low {{ background: #ffca28; }}
        .progress-text {{ font-size: 12px; color: #dce4ec; font-weight: 500; vertical-align: middle; }}
    </style>
</head>
<body>
    <header>
        <div>
            <h1><a href="index.html">Network Hubs</a></h1>
            <span class="meta">Last updated: {last_updated}</span>
        </div>
    </header>
    <nav class="tabs">
        <a href="index.html" class="tab">Scorecard</a>
        <a href="hubs.html" class="tab active">Hubs</a>
    </nav>
    <div class="direction-toggle">
        <button class="dir-btn active" data-direction="outbound" onclick="setDirection('outbound')">Outbound</button>
        <button class="dir-btn" data-direction="inbound" onclick="setDirection('inbound')">Inbound</button>
    </div>
    <div class="published">Published {now.strftime("%-I:%M %p ET, %B %d")} — auto-refreshes every 15 min during ops hours</div>

    <main>
        <div class="hub-jump" id="hub-jump"></div>
        <div id="hubs-container"></div>
    </main>

    <script>
        const HUBS_DATA = {hubs_json};
        const HUB_CODES = {codes_json};
        let CURRENT_DIRECTION = "outbound";

        function escapeHtml(s) {{
            return String(s || "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}})[c]);
        }}

        function setDirection(dir) {{
            CURRENT_DIRECTION = dir;
            document.querySelectorAll(".dir-btn").forEach(b => b.classList.toggle("active", b.dataset.direction === dir));
            renderJump();
            renderHubs();
        }}

        function renderJump() {{
            const el = document.getElementById("hub-jump");
            el.innerHTML = HUB_CODES.map(h => {{
                const rows = (HUBS_DATA[h] || {{}})[CURRENT_DIRECTION] || [];
                return `<a href="#hub-${{h}}">${{h}}<span class="count">(${{rows.length}})</span></a>`;
            }}).join("");
        }}

        function renderOutboundRow(r) {{
            const cls = [];
            if (r.status_raw === "CONTAINER_STATUS_DEPARTED") cls.push("row-departed");
            if (r.is_late) cls.push("row-late");
            else if (r.is_empty) cls.push("row-empty");
            const statusClass = r.status_raw === "CONTAINER_STATUS_DEPARTED" ? "status-departed" : "status-scheduled";
            const lateBadge = r.is_late ? '<span class="late-badge">LATE</span>' : '';
            const emptyBadge = r.is_empty ? '<span class="empty-badge">EMPTY</span>' : '';
            const shipLink = r.shipment_id
                ? `<a class="ship-link" href="https://parcels.doordash.com/network/shipments/${{r.shipment_id}}" target="_blank">${{r.shipment_id}}</a>`
                : '';
            return `<tr class="${{cls.join(' ')}}">
                <td class="dest-cell">${{escapeHtml(r.destination)}}${{lateBadge}}${{emptyBadge}}</td>
                <td class="${{statusClass}}">${{escapeHtml(r.status)}}</td>
                <td>${{escapeHtml(r.dock)}}</td>
                <td>${{escapeHtml(r.scheduled_departure)}}</td>
                <td>${{escapeHtml(r.actual_departure)}}</td>
                <td>${{r.stowed}}</td>
                <td>${{r.eligible}}</td>
                <td>${{escapeHtml(r.equipment)}}</td>
                <td>${{shipLink}}</td>
            </tr>`;
        }}

        function renderInboundRow(r) {{
            const cls = [];
            if (r.status_raw === "INBOUND_TRUCK_SORTATION_STATUS_NOT_STARTED") cls.push("row-not-started");
            if (r.status_raw === "INBOUND_TRUCK_SORTATION_STATUS_DONE_SCANNING") cls.push("row-done");
            const statusMap = {{
                "INBOUND_TRUCK_SORTATION_STATUS_INDUCTING": "status-inducting",
                "INBOUND_TRUCK_SORTATION_STATUS_NOT_STARTED": "status-not-started",
                "INBOUND_TRUCK_SORTATION_STATUS_DONE_SCANNING": "status-done",
            }};
            const statusClass = statusMap[r.status_raw] || "";
            const fillClass = r.progress >= 100 ? "done" : (r.progress < 25 ? "low" : "");
            return `<tr class="${{cls.join(' ')}}">
                <td class="dest-cell">#${{r.set_number}}</td>
                <td class="origin-cell" title="${{escapeHtml(r.origins)}}">${{escapeHtml(r.origins) || '—'}}</td>
                <td class="${{statusClass}}">${{escapeHtml(r.status)}}</td>
                <td>${{r.expected}}</td>
                <td>${{r.inducted}}</td>
                <td>
                    <span class="progress-bar"><span class="progress-fill ${{fillClass}}" style="width:${{Math.min(r.progress, 100)}}%"></span></span>
                    <span class="progress-text">${{r.progress}}%</span>
                </td>
                <td>${{escapeHtml(r.zone_code) || '—'}}</td>
                <td>${{escapeHtml(r.start_time) || '—'}}</td>
            </tr>`;
        }}

        function renderHubs() {{
            const container = document.getElementById("hubs-container");
            container.innerHTML = HUB_CODES.map(hub => {{
                const sides = HUBS_DATA[hub] || {{}};
                const rows = sides[CURRENT_DIRECTION] || [];

                let stats, body;
                if (CURRENT_DIRECTION === "outbound") {{
                    const lateCount = rows.filter(r => r.is_late).length;
                    const emptyCount = rows.filter(r => r.is_empty).length;
                    const departedCount = rows.filter(r => r.status_raw === "CONTAINER_STATUS_DEPARTED").length;
                    stats = `<span class="stat-pill">${{rows.length}} trucks</span>`;
                    stats += `<span class="stat-pill">${{departedCount}} departed</span>`;
                    if (lateCount > 0) stats += `<span class="stat-pill stat-late">${{lateCount}} late</span>`;
                    if (emptyCount > 0) stats += `<span class="stat-pill stat-empty">${{emptyCount}} empty</span>`;
                    if (rows.length === 0) {{
                        body = `<div class="no-data">No outbound trucks for ${{hub}}</div>`;
                    }} else {{
                        body = `<table><thead><tr>
                            <th>Destination</th><th>Status</th><th>Dock</th><th>Scheduled</th><th>Actual</th>
                            <th>Loaded</th><th>Eligible to Load</th><th>Equipment</th><th>Shipment</th>
                        </tr></thead><tbody>` + rows.map(renderOutboundRow).join("") + `</tbody></table>`;
                    }}
                }} else {{
                    const inducting = rows.filter(r => r.status_raw === "INBOUND_TRUCK_SORTATION_STATUS_INDUCTING").length;
                    const notStarted = rows.filter(r => r.status_raw === "INBOUND_TRUCK_SORTATION_STATUS_NOT_STARTED").length;
                    const done = rows.filter(r => r.status_raw === "INBOUND_TRUCK_SORTATION_STATUS_DONE_SCANNING").length;
                    stats = `<span class="stat-pill">${{rows.length}} sets</span>`;
                    if (inducting > 0) stats += `<span class="stat-pill stat-active">${{inducting}} inducting</span>`;
                    if (notStarted > 0) stats += `<span class="stat-pill stat-empty">${{notStarted}} not started</span>`;
                    if (done > 0) stats += `<span class="stat-pill stat-done">${{done}} done</span>`;
                    if (rows.length === 0) {{
                        body = `<div class="no-data">No inbound sortation sets for ${{hub}}</div>`;
                    }} else {{
                        body = `<table><thead><tr>
                            <th>Set #</th><th>Origins</th><th>Status</th><th>Expected</th><th>Inducted</th>
                            <th>Progress</th><th>Zone</th><th>Start</th>
                        </tr></thead><tbody>` + rows.map(renderInboundRow).join("") + `</tbody></table>`;
                    }}
                }}

                return `<section class="hub-section" id="hub-${{hub}}">
                    <div class="hub-header">
                        <div class="hub-name">${{hub}}</div>
                        <div class="hub-stats">${{stats}}</div>
                    </div>
                    ${{body}}
                </section>`;
            }}).join("");
        }}

        renderJump();
        renderHubs();
    </script>
</body>
</html>"""


def publish(dry_run: bool = False, force: bool = False):
    """Fetch data, generate HTML, push to gh-pages."""
    log.info("Fetching scorecard data from localhost...")
    data = fetch_scorecard_data(force=force)
    if not data or not data.get("rows"):
        log.error("No data available — is the dashboard running?")
        return False

    tracked_actions = fetch_tracked_actions()
    html = generate_html(data, tracked_actions)
    output = GH_PAGES_DIR / "index.html"
    output.write_text(html)
    log.info(f"Generated {output} ({len(html)} bytes, {len(data['rows'])} sites)")

    log.info("Fetching hubs data from localhost...")
    hubs_data = fetch_hubs_data(force=force)
    if hubs_data and hubs_data.get("hubs"):
        hubs_html = generate_hubs_html(hubs_data)
        hubs_output = GH_PAGES_DIR / "hubs.html"
        hubs_output.write_text(hubs_html)
        out_count = sum(len(s.get("outbound", [])) for s in hubs_data["hubs"].values())
        in_count = sum(len(s.get("inbound", [])) for s in hubs_data["hubs"].values())
        log.info(f"Generated {hubs_output} ({len(hubs_html)} bytes, {out_count} outbound, {in_count} inbound across {len(hubs_data['hubs'])} hubs)")
    else:
        log.warning("Hubs data unavailable — skipping hubs.html")

    if dry_run:
        log.info("[dry-run] Skipping git push")
        return True

    # Push to gh-pages branch
    try:
        # Check if gh-pages dir is a git repo
        if not (GH_PAGES_DIR / ".git").exists():
            log.info("Initializing gh-pages repo...")
            subprocess.run(["git", "init"], cwd=GH_PAGES_DIR, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "gh-pages"], cwd=GH_PAGES_DIR, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", REPO_URL], cwd=GH_PAGES_DIR, capture_output=True)

        subprocess.run(["git", "add", "index.html", "hubs.html"], cwd=GH_PAGES_DIR, capture_output=True)

        now = datetime.now(ET).strftime("%-I:%M %p ET")
        result = subprocess.run(
            ["git", "commit", "-m", f"Scorecard update {now}"],
            cwd=GH_PAGES_DIR, capture_output=True, text=True,
        )
        if "nothing to commit" in result.stdout:
            log.info("No changes to push")
            return True

        result = subprocess.run(
            ["git", "push", "-f", "origin", "gh-pages"],
            cwd=GH_PAGES_DIR, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            log.info("Pushed to gh-pages successfully")
            return True
        else:
            log.error(f"Push failed: {result.stderr}")
            return False
    except Exception as e:
        log.error(f"Git push failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Publish scorecard to GitHub Pages")
    parser.add_argument("--dry-run", action="store_true", help="Generate HTML only, don't push")
    parser.add_argument("--force", action="store_true", help="Publish even outside ops hours")
    args = parser.parse_args()
    publish(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()

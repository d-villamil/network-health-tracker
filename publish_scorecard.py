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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("publish")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

ET = ZoneInfo("America/New_York")
DASHBOARD_URL = "http://127.0.0.1:5000"
GH_PAGES_DIR = Path(__file__).parent / "gh-pages"
REPO_URL = "https://github.com/d-villamil/network-health-tracker.git"


def fetch_scorecard_data() -> dict | None:
    """Fetch scorecard data from the running localhost dashboard."""
    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/scorecard/refresh", timeout=300)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Failed to fetch from dashboard: {e}")
        return None


def generate_html(data: dict) -> str:
    """Generate a self-contained static HTML scorecard page."""
    rows = data.get("rows", [])
    timeline = data.get("timeline", {})
    last_updated = data.get("last_updated", "Unknown")
    now = datetime.now(ET)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Network Health Scorecard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #2a3d50; color: #e0e6ed; font-size: 14px; }}

        header {{ display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; background: #334d63; border-bottom: 1px solid #456a85; }}
        h1 {{ font-size: 20px; font-weight: 600; }}
        h1 a {{ color: inherit; text-decoration: none; }}
        .meta {{ font-size: 12px; color: #99aabb; margin-left: 12px; }}
        .published {{ font-size: 11px; color: #99aabb; padding: 8px 24px; background: #263849; text-align: center; }}

        main {{ padding: 16px 24px; }}

        .section {{ margin-bottom: 32px; }}
        .section-title {{ font-size: 16px; font-weight: 600; color: #e0e6ed; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 2px solid #456a85; }}
        .section-count {{ font-size: 13px; font-weight: 400; color: #99aabb; }}

        table {{ width: 100%; border-collapse: collapse; background: #334d63; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }}
        thead {{ background: #263849; }}
        th {{ padding: 10px 12px; text-align: left; font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; color: #b0c0d0; cursor: pointer; user-select: none; }}
        th:hover {{ background: #456a85; }}
        th.sorted-asc::after {{ content: " \\25B2"; font-size: 10px; }}
        th.sorted-desc::after {{ content: " \\25BC"; font-size: 10px; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #456a85; font-variant-numeric: tabular-nums; color: #dce4ec; }}
        tbody tr:nth-child(even) {{ background: #385268; }}
        tbody tr:nth-child(odd) {{ background: #334d63; }}
        tbody tr:hover {{ background: #426280 !important; }}

        .th-sub {{ font-size: 10px; font-weight: 400; color: #7799aa; text-transform: none; letter-spacing: 0; }}
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
    </style>
</head>
<body>
    <header>
        <div>
            <h1><a href="https://docs.google.com/spreadsheets/d/1cQH7gwBvAmZO8WiYNPbrCSnO8zxB0DUDhzaeMM5o-ZU/edit?gid=11136630#gid=11136630" target="_blank">Network Health Scorecard</a></h1>
            <span class="meta">Last updated: {last_updated}</span>
        </div>
        <select id="pod-filter" onchange="renderTable()">
            <option value="all">All Regions</option>
            <option value="Northeast">Northeast</option>
            <option value="Southeast">Southeast</option>
            <option value="Central">Central</option>
            <option value="West">West</option>
        </select>
    </header>
    <div class="published">Published {now.strftime("%-I:%M %p ET, %B %d")} — auto-refreshes every 30 min during ops hours</div>

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
                <select id="track-exception" required>
                    <option value="">Select exception...</option>
                    <option value="Needs Replan">Needs Replan</option>
                    <option value="Small Batch">Small Batch</option>
                    <option value="Return Bin">Return Bin</option>
                    <option value="Dispatch">Dispatch</option>
                </select>
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
        const WEBHOOK_URL = 'https://script.google.com/a/macros/doordash.com/s/AKfycbxmZFP9sfjLI6RKGdDAsfQEjdMozuNhnQqDHTs8SgeY4twnQqH-BMQ0aUa4g9VJF6RRhQ/exec';
        const DATA = {json.dumps(rows)};
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
            let rows = DATA;
            if (filter !== 'all') rows = rows.filter(r => r.pod === filter);

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

                regionRows.forEach(r => {{
                    const tr = document.createElement('tr');
                    const siteTimeline = TIMELINE[r.site];
                    const hasAlerts = siteTimeline && siteTimeline.events && siteTimeline.events.length > 0;
                    const isExpanded = expandedSites.has(r.site);

                    tr.classList.add('expandable');
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
                        <td class="cell-site">${{expandIcon}}<a href="https://parcels.doordash.com/exceptions?facility_code=${{r.site}}" target="_blank">${{r.site}}</a> ${{alertBadge}}</td>
                        <td>${{r.pod}}</td>
                        <td class="cet-cell">${{cetCell}}</td>
                        <td class="${{sbClass}}">${{r.small_batches || '<span class="no-data">0</span>'}}</td>
                        <td class="${{scanClass}}" title="${{scanTitle}}">${{r.scan_start || '<span class="no-data">—</span>'}}</td>
                        <td class="${{dispatchClass}}" title="${{dispatchTitle}}">${{r.dispatch_active ? '<span class="dispatch-dot"></span>' : ''}}${{r.dispatch_start || '<span class="no-data">—</span>'}}</td>
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
            document.getElementById('track-notes').value = '';
            document.getElementById('track-analyst').value = localStorage.getItem('dashboard_analyst') || '';
            document.getElementById('modal-overlay').classList.remove('hidden');
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
                analyst: analyst,
                action: document.getElementById('track-exception').value,
                needs_replan: document.getElementById('track-replan').value,
                small_batches: document.getElementById('track-sb').value,
                return_bin: document.getElementById('track-rb').value,
                plib: document.getElementById('track-plib').value,
                notes: document.getElementById('track-notes').value,
            }});
            window.open(WEBHOOK_URL + '?' + params.toString(), '_blank');
            hideModal();
        }}

        renderTable();
    </script>
</body>
</html>"""


def publish(dry_run: bool = False):
    """Fetch data, generate HTML, push to gh-pages."""
    log.info("Fetching scorecard data from localhost...")
    data = fetch_scorecard_data()
    if not data or not data.get("rows"):
        log.error("No data available — is the dashboard running?")
        return False

    html = generate_html(data)
    output = GH_PAGES_DIR / "index.html"
    output.write_text(html)
    log.info(f"Generated {output} ({len(html)} bytes, {len(data['rows'])} sites)")

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

        subprocess.run(["git", "add", "index.html"], cwd=GH_PAGES_DIR, capture_output=True)

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
    args = parser.parse_args()
    publish(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

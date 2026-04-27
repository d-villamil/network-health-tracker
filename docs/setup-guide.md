# Network Health Dashboard — Setup Guide

Follow this guide to set up the dashboard on your Mac so it auto-publishes the GitHub Pages scorecard.

---

## Prerequisites

- Mac with Python 3.11+
- `parcel-cli` installed at `/usr/local/bin/parcel-cli`
- DoorDash Google account (for Sheets access)
- Push access to https://github.com/d-villamil/network-health-tracker (ask David to add you as collaborator)

---

## Step 1: Clone the Repo

```bash
git clone https://github.com/d-villamil/network-health-tracker.git
cd network-health-tracker
```

## Step 2: Set Up Python Environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Step 3: Auth Files

You need 3 files that are NOT in the repo (they contain credentials). Get these from David:

1. **`.env`** — place in the project root
   ```
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
   TRINO_USER=your.name@doordash.com
   ```

2. **`credentials.json`** — Google OAuth2 credentials, place in the project root

3. **`token.json`** — will be auto-generated on first run (browser will open for Google auth)

## Step 4: Authenticate parcel-cli

```bash
parcel-cli auth login
```

This opens a browser for DoorDash SSO. Once logged in, you're good for ~48 hours. The dashboard auto-detects expiration and re-opens the browser when needed.

## Step 5: Authenticate Trino

Run any command that triggers Trino — a browser will open for auth:

```bash
.venv/bin/python -c "from return_bin_tracker import run; run()"
```

Complete the browser auth. Token caches locally for ~48 hours.

## Step 6: Authenticate Google Sheets

Run the dashboard once — it will open a browser for Google OAuth on first use:

```bash
.venv/bin/python dashboard.py
```

Open http://127.0.0.1:5000 and wait for it to load. Once it loads, Sheets auth is cached in `token.json`.

Press Ctrl+C to stop after confirming it works.

## Step 7: Start the Dashboard

```bash
.venv/bin/python dashboard.py &
```

Keep this running. The dashboard serves at http://127.0.0.1:5000.

## Step 8: Set Up Auto-Publish (GitHub Pages)

This publishes the scorecard to https://d-villamil.github.io/network-health-tracker/ every 15 minutes during ops hours (5 AM – 2 PM ET).

```bash
# Symlink the launchd plist
ln -sf $(pwd)/launchd/com.doordash.parcel-cli-publish.plist ~/Library/LaunchAgents/

# Load it
launchctl load ~/Library/LaunchAgents/com.doordash.parcel-cli-publish.plist

# Verify it's loaded
launchctl list | grep parcel-cli-publish
```

## Step 9: Set Up Daily Snapshot (Optional)

Saves end-of-day scorecard + timeline to a Google Sheet at 6 PM ET:

```bash
ln -sf $(pwd)/launchd/com.doordash.parcel-cli-snapshot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.doordash.parcel-cli-snapshot.plist
```

---

## Daily Operations

### Starting your day
1. Open Terminal
2. `cd ~/network-health-tracker`
3. `.venv/bin/python dashboard.py &`
4. The auto-publish launchd handles the rest

### If auth expires (browser popup)
- **parcel-cli**: Auto-opens browser — just complete the SSO login
- **Trino**: Browser opens for OAuth — complete it and the query retries
- **Google Sheets**: Rarely expires, but if it does, delete `token.json` and restart the dashboard

### Manual publish (anytime)
```bash
cd ~/network-health-tracker && .venv/bin/python publish_scorecard.py --force
```

### Check publish logs
```bash
tail -20 ~/network-health-tracker/logs/launchd-publish.log
```

### Check snapshot logs
```bash
tail -10 ~/network-health-tracker/logs/launchd-snapshot.log
```

---

## Troubleshooting

### Dashboard not loading
```bash
# Check if it's running
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/

# If not, restart
pkill -f dashboard.py
.venv/bin/python dashboard.py &
```

### GitHub Pages not updating
```bash
# Check launchd status
launchctl list | grep parcel-cli-publish

# Check logs
tail -20 logs/launchd-publish.log

# Manual publish
.venv/bin/python publish_scorecard.py --force
```

### parcel-cli auth expired
```bash
parcel-cli auth login
# Complete browser SSO
parcel-cli status
# Should show "Authenticated"
```

### Trino auth expired
The dashboard will show empty Return Bin and baseline data. A browser popup will appear — complete the OAuth flow and refresh.

### Google Sheets auth expired
```bash
rm token.json
# Restart dashboard — browser will open for re-auth
.venv/bin/python dashboard.py
```

### Port 5000 already in use
```bash
# Find what's using it
lsof -i :5000
# Kill it
pkill -f dashboard.py
# Restart
.venv/bin/python dashboard.py &
```

---

## Architecture

```
network-health-tracker/
├── dashboard.py          ← Flask app (localhost:5000)
├── publish_scorecard.py  ← Generates static HTML → pushes to GitHub Pages
├── daily_snapshot.py     ← End-of-day export to Google Sheet
├── scorecard_tracker.py  ← CET + Trino baselines
├── exception_tracker.py  ← Needs replan, missing, etc. from parcel-cli
├── lfr_tracker.py        ← LFR batches, PLIB, dispatch status
├── return_bin_tracker.py ← Scan return bin from Trino
├── cet_tracker.py        ← Late shipments from parcel-cli
├── baseline_tracker.py   ← 30-day avg scan/dispatch from Trino
├── timeline_tracker.py   ← Threshold crossing events
├── site_flags_tracker.py ← Slack channel flags (manually cached)
├── config.yaml           ← Site regions, thresholds
├── templates/            ← HTML templates for localhost
├── static/               ← CSS
├── sql/                  ← Trino queries
├── state/                ← Cached state files (gitignored)
├── logs/                 ← Log files (gitignored)
├── gh-pages/             ← Static site for GitHub Pages (separate git repo)
└── launchd/              ← Schedule plists
```

### Data Flow
1. **parcel-cli** → real-time exceptions, batches, shipments, dispatch status
2. **Trino** → return bin counts, 30-day scan/dispatch baselines
3. **Dashboard** → merges all data, serves localhost
4. **Publisher** → fetches from dashboard API, generates static HTML, pushes to GitHub Pages
5. **GitHub Pages** → team accesses at https://d-villamil.github.io/network-health-tracker/

---

## Questions?
Reach out to David Villamil on Slack.

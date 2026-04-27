# Network Health Scorecard — User Guide

## What Is It?
A real-time dashboard that surfaces site health across the DashLink spoke network. It pulls live data from the sort app (parcel-cli), Trino, and Slack to give the Live Ops team a single view of what needs attention.

**Live URL:** https://d-villamil.github.io/network-health-tracker/

The scorecard auto-updates every 15 minutes during ops hours (5 AM – 2 PM ET). All times shown are in the site's local timezone.

---

## Layout

Sites are grouped by region: **Northeast, Southeast, Central, West**. Each row represents one spoke.

### Filters (top right)
- **Dispatch filter** — Show all sites, only sites with dispatch on, or only sites with dispatch off
- **Site search** — Type a site code to jump to a specific site
- **Region filter** — Filter by Northeast, Southeast, Central, or West

---

## Columns

### Site
The site code (e.g., DET-13). Click to open the exceptions page in the sort app.

**Status dots (left of site name):**
| Dot | Meaning |
|-----|---------|
| 🟢 Green | Site is currently OPEN |
| ⚫ Gray | Site is currently CLOSED |

**Alert badge (red number):** Shows how many threshold crossings have been logged today. Click the row to expand and see the timeline.

### Region
Northeast, Southeast, Central, or West.

### CET Met
Shows each inbound truck and whether it met CET (Critical Entry Time).

| Icon | Meaning |
|------|---------|
| ✅ | Truck arrived on time (within 30 min of CET) |
| ❌ | Truck arrived 30+ min late — click to open shipment details |
| ⏳ | CET has passed but truck hasn't arrived yet |

Late trucks show minutes late and the exception reason (e.g., "104m late (Yard Gate Congestion)").

**Row border colors:**
| Border | Meaning |
|--------|---------|
| Red left border | 0% CET met (all trucks late or pending) |
| Yellow left border | Partial CET met (some on time, some late) |

### Small Batches
Count of active batches with fewer than 15 parcels.

| Color | Threshold |
|-------|-----------|
| Yellow | 6 or more small batches |
| No highlight | 5 or fewer |

### Sort Scan Start
When the sort operation started — defined as the time of the 25th successful parcel scan at the spoke (excludes missing/wrong facility scans).

| Color | Meaning |
|-------|---------|
| Green | Within 15 min of the 30-day average start time |
| Red | 16+ min later than the 30-day average |
| No color | No data yet or no average available |

Hover to see: average start time and the difference in minutes.

### Dispatch Start
When dispatch began — defined as the 10th parcel entering "Looking for Runners" state (from 30-day Trino baseline).

| Dot | Meaning |
|-----|---------|
| 🟢 Green dot | Dispatch toggle is ON (runners being assigned) |
| 🟡 Yellow dot | Dispatch toggle is OFF but runners are still active (lingering activity) |
| No dot | No dispatch activity |

| Color | Meaning |
|-------|---------|
| Green | Within 15 min of the 30-day average dispatch start |
| Red | 16+ min later than the 30-day average |

Hover to see: average dispatch time and the difference in minutes.

### Needs Replan
Current count of parcels in EXCEPTION_TYPE_NEED_REPLAN status — parcels that need a replan to get into batches.

| Color | Threshold |
|-------|-----------|
| Yellow | 15 – 34 |
| Red | 35+ |

### Return Bin
Count of parcels in RETURNED_TO_BIN state (from Trino). These are parcels scanned back into the return bin.

| Color | Threshold |
|-------|-----------|
| Yellow | 5 – 14 |
| Red | 15+ |

### LFR > 45m
Number of batches that have been in "Looking for Runners" / "Preparing" / "Ready to Dispatch" status for more than 45 minutes.

| Color | Threshold |
|-------|-----------|
| Yellow | 1 or more batches stuck > 45 min |

### PLIB (Packages Left in Building)
Total parcels still in the building = parcels in PREPARING/READY_TO_DISPATCH batches + needs replan count.

| Color | Threshold |
|-------|-----------|
| Yellow | 15 – 29 |
| Red | 30+ |

### Flags
Aggregated mentions from Slack channels and trackers for today. Each tag links to the source.

| Tag | Source |
|-----|--------|
| **AP** (red) | #ask-parcels — engineering escalations |
| **Network** (yellow) | #parcel-network-ops — network-wide alerts |
| **Transport** (blue) | #parcel-transportation and war room |
| **Tracker** (green) | LiveOps tracking sheets |

---

## Expandable Rows

Click any site row to expand it. The expanded section shows:

1. **Timeline** — Every threshold crossing logged today with timestamps (e.g., "8:15 AM — Needs Replan hit 18"). Newest events first.
2. **Tracked actions** — If someone has used Track It on this site, their name and action type appear (e.g., "✅ david villamil — Needs Replan").
3. **Track It button** — Log an action for this site.

### Tracked Rows
Sites that have been actioned show:
- **Green left border** with muted/faded row — someone already tracked it
- Expand to see who and what action was taken

---

## Track It

Use Track It to log an action taken on a site. This creates a record in the shared Google Sheet.

**Steps:**
1. Click a site row to expand it
2. Click **Track It**
3. Fill in:
   - **Analyst Name** (remembered for next time)
   - **Exception Type** (Needs Replan, Small Batch, Return Bin, PLIB, Dispatch)
   - **Quantity** auto-fills based on exception type
   - **Notes** — what you did, how it impacted the value
4. Submit — opens a new tab confirming the track, row is logged to the shared sheet

**Tracked Actions Sheet:** https://docs.google.com/spreadsheets/d/1cQH7gwBvAmZO8WiYNPbrCSnO8zxB0DUDhzaeMM5o-ZU

---

## Threshold Summary

| Metric | Yellow | Red |
|--------|--------|-----|
| CET Met | Partial (some late) | 0% (all late) |
| Small Batches | 6+ | — |
| Sort Scan Start | — | 16+ min late vs avg |
| Dispatch Start | — | 16+ min late vs avg |
| Needs Replan | 15+ | 35+ |
| Return Bin | 5+ | 15+ |
| LFR > 45m | 1+ batch | — |
| PLIB | 15+ | 30+ |

---

## Data Sources

| Data | Source | Refresh |
|------|--------|---------|
| Exceptions (needs replan) | parcel-cli (Unified Gateway API) | Every 3 min |
| CET / Shipments | parcel-cli shipment list | Every 3 min |
| Sort Scan Start / Dispatch Start baselines | Trino (30-day avg) | Every scorecard refresh |
| Return Bin | Trino (RETURNED_TO_BIN) | Every 30 min |
| Batch status (LFR, PLIB, dispatch) | parcel-cli batch list | Every 3 min |
| Operating status (open/closed) | parcel-cli facility list | Every scorecard refresh |
| Flags | Slack channels + Google Sheets | Manually refreshed daily |
| GitHub Pages | Published from localhost | Every 15 min (5 AM – 2 PM ET) |

---

---

## Team Assignments

### Early Shift (5-7 AM ET) — Sort Focus: NE, SE, Central

| Analyst | Shift | Region Assignment |
|---------|-------|-------------------|
| Tim | 5AM-1:30PM | NE/SE Sort + Dispatch |
| Miuris | 4AM-9AM | SE Sort |
| Julio | 5AM-1:30PM | NE/SE/Central Sort + Dispatch |
| Jose | 5AM-1:30PM | NE/SE/Central Sort + Dispatch |
| Andrea | 6AM-2:30PM | NE/SE Sort + Dispatch |
| Tatiana | 6AM-2:30PM | NE/SE Sort + Dispatch |
| Nikihita | 6AM-2:30PM | SE/Central Sort + Dispatch |

### Mid Shift (8-10 AM ET) — Central/West Blend + Network Dispatch

| Analyst | Shift | Region Assignment |
|---------|-------|-------------------|
| Amber | 8AM-4:30PM | Central/West Sort + Network Dispatch |
| Marina | 8AM-4:30PM | Central/West Sort + Network Dispatch |
| Adolfo | 8AM-4:30PM | West Sort + Network Dispatch |

### Late Shift (11 AM+) — West + Network Dispatch

| Analyst | Shift | Region Assignment |
|---------|-------|-------------------|
| Cas | 11AM-7:30PM | West Sort + Network Dispatch |
| Keerthi | 11AM-7:30PM | West Sort + Network Dispatch |

### Evening/Overnight — Transportation, PLIB & Hub Ops

| Analyst | Shift | Region Assignment |
|---------|-------|-------------------|
| Lynnzey | 2PM-10:30PM | Network Transportation + PLIB + Hub Ops |
| Brigette | 3PM-1AM | Network Transportation + PLIB + Hub Ops |
| Nicole | 4PM-12:30AM | Hub Ops / Transportation + PLIB |
| Carey | 5PM-1:30AM | Hub Ops / Transportation + PLIB |
| Mariela | 9PM-5:30AM | Overnight Ops |
| Gabriel | 9PM-5:30AM | Overnight Ops |

---

## Questions?
Reach out to David Villamil on Slack.

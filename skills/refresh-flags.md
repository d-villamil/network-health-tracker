---
allowed-tools: mcp__slack__slack_read_channel, mcp__slack__slack_search_public, mcp__google-workspace__googleSheet, Bash(cd /Users/david.villamil/parcel-cli && .venv/bin/python *), Read, Write
description: Refresh site flags from Slack channels and update the dashboard
---

Refresh the site flags cache for the Network Health Dashboard by reading today's messages from all monitored Slack channels.

## Steps

1. Read today's messages from these Slack channels:
   - #ask-parcels (C04LU23A27N) → category "AP"
   - #parcel-network-ops (C07MT9HR508) → category "Network"
   - #parcel-transportation (C03Q2RRQFDL) → category "Transport"
   - #parcel-transportation-war-room (C07M6AR7PF1) → category "Transport"

2. Read today's entries from these Google Sheets:
   - LiveOps Tracking Sheet (1l9RP11U1oFdNfLwpJmplQHceh8zfaT5pumPPAC8Ys9g) → tabs "Pass Down" and "Roll Tracking" → category "Tracker"
   - Escalation Tracker (1BTL59TYgUiowK6feXIcXzOsDiVz20so-l89VP2zH6Ug) → category "Tracker"

3. For each Slack channel, extract messages from today (EDT timezone) that mention site codes (pattern: 2-4 uppercase letters + dash + 1-2 digits, e.g., DET-13, LAX-11).

4. For each Google Sheet, look for rows with today's date that mention site codes in any column.

5. For each site mention found, create a flag entry with:
   - category (AP, Network, Transport, Tracker)
   - preview (first 80 chars of message/row content, include priority if present)
   - link to the Slack message or Google Sheet

6. Write the flags to `state/site_flags_today.json` in this format:
```json
{
  "date": "YYYY-MM-DD",
  "sites": {
    "SITE-CODE": [
      {"category": "AP", "preview": "P0: description...", "link": "https://..."}
    ]
  }
}
```

5. After caching, run: `cd /Users/david.villamil/parcel-cli && .venv/bin/python publish_scorecard.py`

6. Report what was found: how many flags across how many sites, and confirm the publish succeeded.

## Important
- Only include messages from TODAY (check the EDT timestamp)
- Extract site codes using regex pattern `[A-Z]{2,4}-\d{1,2}`
- Skip Slackbot reminders and topic changes
- Include the priority level (P0, P1, P2, P3) in the preview when present
- The Slack message link format is: `https://doordash.enterprise.slack.com/archives/{CHANNEL_ID}/p{TIMESTAMP_WITHOUT_DOT}`
- For Google Sheets, link to the sheet URL with the appropriate gid
- LiveOps Tracking Sheet Pass Down tab gid: `2051267341`
- LiveOps Tracking Sheet Roll Tracking tab gid: `0`
- Escalation Tracker gid: `1233423933`
- Deduplicate: if the same site+category already exists, don't add again

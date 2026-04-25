"""
Site flags tracker — searches Slack channels and Google Sheets for today's site mentions.
Groups results by category: AP (ask-parcels), Transport, Tracker.
"""

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
STATE_FILE = Path(__file__).parent / "state" / "site_flags_today.json"

# Site code pattern
SITE_PATTERN = re.compile(r'\b([A-Z]{2,4}-\d{1,2})\b')

# Slack channels grouped by category
SLACK_SOURCES = {
    "AP": {
        "channel_id": "C04LU23A27N",
        "name": "ask-parcels",
    },
    "Network": {
        "channel_id": "C07MT9HR508",
        "name": "parcel-network-ops",
    },
    "Transport": [
        {
            "channel_id": "C03Q2RRQFDL",
            "name": "parcel-transportation",
        },
        {
            "channel_id": "C07M6AR7PF1",
            "name": "parcel-transportation-war-room",
        },
    ],
}


def _load_cached() -> dict | None:
    """Load today's cached flags."""
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if data.get("date") == str(date.today()):
            return data.get("sites", {})
    return None


def _save_cache(sites: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"date": str(date.today()), "sites": sites}, indent=2))


def search_slack_for_sites(slack_search_func, sites: set[str]) -> dict:
    """
    Search Slack channels for today's site mentions.
    slack_search_func should be a callable that searches Slack.
    Returns {site: [{category, message_preview, link}]}
    """
    site_flags: dict[str, list] = {}

    # Search ask-parcels
    _search_channel(slack_search_func, "C04LU23A27N", "AP", sites, site_flags)

    # Search parcel-network-ops
    _search_channel(slack_search_func, "C07MT9HR508", "Network", sites, site_flags)

    # Search transportation channels
    _search_channel(slack_search_func, "C03Q2RRQFDL", "Transport", sites, site_flags)
    _search_channel(slack_search_func, "C07M6AR7PF1", "Transport", sites, site_flags)

    return site_flags


def _search_channel(search_func, channel_id: str, category: str, sites: set[str], site_flags: dict):
    """Search a single channel for site mentions today."""
    try:
        results = search_func(channel_id)
        if not results:
            return

        for msg in results:
            text = msg.get("text", "")
            ts = msg.get("ts", "")
            found_sites = SITE_PATTERN.findall(text)

            for site in found_sites:
                if site not in sites:
                    continue

                # Build Slack message link
                link = f"https://doordash.enterprise.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
                preview = text[:80].replace("\n", " ")

                if site not in site_flags:
                    site_flags[site] = []

                # Avoid duplicate category entries for same message
                site_flags[site].append({
                    "category": category,
                    "preview": preview,
                    "link": link,
                })
    except Exception as e:
        log.warning(f"Failed to search channel {channel_id}: {e}")


def build_flags_from_channel_messages(all_sites: set[str], channel_messages: dict[str, list]) -> dict:
    """
    Build site flags from pre-fetched channel messages.
    channel_messages: {channel_id: [list of message dicts with 'text' and 'ts']}
    Returns {site: [{category, preview, link}]}
    """
    CHANNEL_CATEGORIES = {
        "C04LU23A27N": "AP",
        "C07MT9HR508": "Network",
        "C03Q2RRQFDL": "Transport",
        "C07M6AR7PF1": "Transport",
    }

    site_flags: dict[str, list] = {}

    for channel_id, messages in channel_messages.items():
        category = CHANNEL_CATEGORIES.get(channel_id, "Other")

        for msg in messages:
            text = msg.get("text", "")
            ts = msg.get("ts", "")
            found_sites = SITE_PATTERN.findall(text)

            for site in found_sites:
                if site not in all_sites:
                    continue

                link = f"https://doordash.enterprise.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
                preview = text[:80].replace("\n", " ")

                if site not in site_flags:
                    site_flags[site] = []

                # Dedupe: don't add same category+link twice
                existing = {(f["category"], f["link"]) for f in site_flags[site]}
                if (category, link) not in existing:
                    site_flags[site].append({
                        "category": category,
                        "preview": preview,
                        "link": link,
                    })

    _save_cache(site_flags)
    log.info(f"Site flags: {sum(len(v) for v in site_flags.values())} flags across {len(site_flags)} sites")
    return site_flags


def get_flags(all_sites: set[str], slack_read_func=None) -> dict:
    """
    Get site flags. Uses cache if available.
    slack_read_func: optional callable(channel_id) -> list of messages
    Returns {site: [{category, preview, link}]}
    """
    cached = _load_cached()
    if cached is not None:
        return cached

    if slack_read_func:
        flags = search_slack_for_sites(slack_read_func, all_sites)
        _save_cache(flags)
        return flags

    return {}

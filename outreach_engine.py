"""
Outreach engine — evaluates exception thresholds and generates site outreach messages.
Tracks cooldown to avoid re-sending to the same site within a configurable window.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent / "state" / "outreach_cooldown.json"
ET = ZoneInfo("America/New_York")


def _load_cooldown() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_cooldown(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _is_on_cooldown(site: str, cooldown_minutes: int, state: dict) -> bool:
    """Check if a site was already sent an outreach within the cooldown window."""
    entry = state.get(site)
    if not entry:
        return False
    last_sent = datetime.fromisoformat(entry["last_sent"])
    return datetime.now(ET) - last_sent < timedelta(minutes=cooldown_minutes)


def evaluate(results: list[dict], outreach_cfg: dict) -> list[dict]:
    """
    Evaluate exception results against outreach thresholds.
    Returns a list of outreach actions to take.
    """
    replan_cfg = outreach_cfg.get("needs_replan", {})
    red_threshold = replan_cfg.get("red", 35)
    yellow_threshold = replan_cfg.get("yellow", 15)
    cooldown_minutes = outreach_cfg.get("cooldown_minutes", 120)
    template = replan_cfg.get("message_template", "")

    cooldown_state = _load_cooldown()
    now = datetime.now(ET)
    time_str = now.strftime("%-I:%M %p ET")

    actions = []

    for r in results:
        count = r.get("needs_replan", 0)
        site = r["site"]

        if count < yellow_threshold:
            continue

        if count >= red_threshold:
            severity = "red"
        else:
            severity = "yellow"

        # Only generate outreach drafts for red severity
        if severity != "red":
            log.info(f"  {site}: needs_replan={count} (yellow, no outreach)")
            continue

        if _is_on_cooldown(site, cooldown_minutes, cooldown_state):
            last = cooldown_state[site]["last_sent"]
            log.info(f"  {site}: needs_replan={count} (red, but on cooldown since {last})")
            continue

        message = template.format(
            site_code=site,
            count=count,
            time=time_str,
        ).strip()

        actions.append({
            "site": site,
            "pod": r["pod"],
            "count": count,
            "delta": r.get("delta", 0),
            "severity": severity,
            "message": message,
            "timestamp": time_str,
        })

    log.info(f"Outreach evaluation: {len(actions)} sites need outreach (red >= {red_threshold})")
    return actions


def record_sent(sites: list[str]) -> None:
    """Record that outreach was sent for these sites (updates cooldown state)."""
    state = _load_cooldown()
    now = datetime.now(ET).isoformat()
    for site in sites:
        state[site] = {"last_sent": now}
    _save_cooldown(state)
    log.info(f"Cooldown updated for {len(sites)} sites")

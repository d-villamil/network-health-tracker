"""
Slack client for posting outreach drafts via Incoming Webhooks.
Reads SLACK_WEBHOOK_URL from environment.
"""

import logging
import os
import time

import requests

log = logging.getLogger(__name__)


class SlackClient:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.webhook_url = os.environ.get("SLACK_WEBHOOK_URL", cfg.get("webhook_url", ""))
        self.channel = cfg.get("channel", "#parcel-claude")
        self.dry_run = dry_run
        self.max_retries = 2

    def _format_outreach(self, action: dict) -> str:
        """Format an outreach action into a Slack message."""
        delta_str = f"{action['delta']:+d}" if action["delta"] != 0 else "no change"

        lines = [
            f":clipboard: *Outreach Draft — {action['site']}* ({action['pod']})",
            f"━━━━━━━━━━━━━━━━━━",
            f"*Needs Replan:* {action['count']} ({delta_str} vs last run)",
            f"*Time:* {action['timestamp']}",
            "",
            "*Copy to Intercom:*",
            f">{action['message']}",
            "",
            "_Status: Pending outreach_",
        ]
        return "\n".join(lines)

    def send_outreach(self, actions: list[dict]) -> list[str]:
        """
        Post outreach drafts to Slack. Returns list of sites that were sent.
        """
        if not actions:
            log.info("No outreach actions to send")
            return []

        sent_sites = []

        for action in actions:
            message = self._format_outreach(action)

            if self.dry_run:
                log.info(f"[DRY RUN] Would post outreach for {action['site']}:\n{message}\n{'—'*40}")
                sent_sites.append(action["site"])
                continue

            if not self.webhook_url or self.webhook_url.startswith("${"):
                log.error("SLACK_WEBHOOK_URL is not set — cannot send outreach")
                return sent_sites

            payload = {"text": message}
            last_exc = None

            for attempt in range(1, self.max_retries + 2):
                try:
                    resp = requests.post(self.webhook_url, json=payload, timeout=10)
                    resp.raise_for_status()
                    log.info(f"Outreach posted to Slack: {action['site']} (replan={action['count']})")
                    sent_sites.append(action["site"])
                    break
                except Exception as exc:
                    last_exc = exc
                    log.warning(f"Slack attempt {attempt}/{self.max_retries + 1} failed: {exc}")
                    if attempt <= self.max_retries:
                        time.sleep(3 * attempt)
            else:
                log.error(f"All Slack retries exhausted for {action['site']}: {last_exc}")

        log.info(f"Outreach sent for {len(sent_sites)} sites")
        return sent_sites

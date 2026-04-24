"""
Return Bin tracker — queries Trino for RETURNED_TO_BIN parcel counts per site.
Uses Trino because parcel-cli's API caps at 1500 rows and misses these parcels.
"""

import logging
import os
import time
from pathlib import Path

import trino
import trino.auth

log = logging.getLogger(__name__)

SQL_FILE = Path(__file__).parent / "sql" / "scan_return_bin.sql"

TRINO_HOST = "trino.doordash.team"
TRINO_PORT = 443


def _connect():
    user = os.environ.get("TRINO_USER", "")
    token = os.environ.get("TRINO_TOKEN")
    if token:
        auth = trino.auth.JWTAuthentication(token)
    else:
        auth = trino.auth.OAuth2Authentication()

    return trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=user,
        catalog="datalake",
        http_scheme="https",
        auth=auth,
        verify=True,
    )


def run() -> list[dict]:
    """Query Trino for scan_return_bin counts per site. Returns list of dicts."""
    sql = SQL_FILE.read_text()

    for attempt in range(1, 4):
        try:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            log.info(f"Trino scan_return_bin OK: {len(rows)} sites, attempt {attempt}")
            return rows
        except Exception as e:
            log.warning(f"Trino attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(5 * attempt)

    log.error("Trino scan_return_bin failed after 3 attempts")
    return []

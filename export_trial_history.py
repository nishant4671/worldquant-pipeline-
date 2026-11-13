"""Standalone trial history exporter for WorldQuant BRAIN alphas."""

import csv
import logging
import os
from pathlib import Path
import time
from typing import Any

import api_client
import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("export_trial_history")


def export_trial_history(output_csv: str | Path = "reports/quant_audit/trial_history.csv") -> int:
    """Fetch daily PnL history for all alphas in the database and export to CSV.

    Args:
        output_csv: Target CSV file path for trial history.

    Returns:
        int: Number of alphas successfully exported.
    """
    target = database._ensure_db()
    with database.connect(target) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT alpha_id FROM alphas WHERE alpha_id IS NOT NULL AND alpha_id != ''")
        rows = cursor.fetchall()
        alpha_ids = [str(row["alpha_id"]) for row in rows]

    log.info("Found %d alpha candidates in database for PnL history export.", len(alpha_ids))

    client = api_client.BrainClient()
    pnl_data: dict[str, list[tuple[str, Any]]] = {}
    all_dates: set[str] = set()

    for idx, alpha_id in enumerate(alpha_ids, start=1):
        log.info("[%d/%d] Fetching PnL history for alpha_id: %s", idx, len(alpha_ids), alpha_id)
        try:
            resp = None
            # Support both /recordsets/pnl and /records/pnl endpoints
            for endpoint in (f"/alphas/{alpha_id}/recordsets/pnl", f"/alphas/{alpha_id}/records/pnl"):
                try:
                    candidate = client.request("GET", endpoint)
                    if candidate and candidate.status_code == 200 and candidate.text.strip():
                        resp = candidate
                        break
                except Exception as endpoint_err:
                    log.debug("Endpoint %s failed for %s: %s", endpoint, alpha_id, endpoint_err)
                    continue

            if not resp or resp.status_code != 200 or not resp.text.strip():
                log.warning("Skipping alpha %s: Failed to retrieve PnL records (non-200 or empty).", alpha_id)
                time.sleep(1)
                continue

            data = resp.json()
            records: list[Any] = []
            if isinstance(data, dict):
                records = data.get("records") or data.get("data") or data.get("results") or []
            elif isinstance(data, list):
                records = data

            entries: list[tuple[str, Any]] = []
            for item in records:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    dt = str(item[0])
                    pnl_val = item[1]
                elif isinstance(item, dict):
                    dt = str(item.get("date") or item.get("Date") or item.get("timestamp") or "")
                    pnl_val = item.get("pnl") if "pnl" in item else item.get("value", 0.0)
                else:
                    continue

                if dt:
                    all_dates.add(dt)
                    entries.append((dt, pnl_val))

            if entries:
                pnl_data[alpha_id] = entries
                log.info("Successfully fetched %d daily PnL records for %s.", len(entries), alpha_id)
            else:
                log.warning("No PnL records parsed for alpha %s.", alpha_id)

        except Exception as exc:
            log.warning("Error fetching PnL for alpha %s: %s", alpha_id, exc)

        time.sleep(1)

    sorted_dates = sorted(all_dates)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    alpha_cols = list(pnl_data.keys())

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["Date"] + alpha_cols
        writer.writerow(header)

        alpha_maps = {aid: dict(records) for aid, records in pnl_data.items()}
        for dt in sorted_dates:
            row = [dt] + [alpha_maps[aid].get(dt, 0.0) for aid in alpha_cols]
            writer.writerow(row)

    count = len(alpha_cols)
    print(f"Successfully exported {count} alphas to trial_history.csv")
    return count


if __name__ == "__main__":
    export_trial_history()

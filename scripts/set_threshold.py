#!/usr/bin/env python3
"""
Sets (or clears) the "alert near low" threshold for one tracked URL.

Run manually via `python scripts/set_threshold.py`, or by
.github/workflows/set-threshold.yml (workflow_dispatch), which is what
the price table's web page calls.

Reads:
  PRODUCT_LINK       - required, the Amazon URL the threshold applies to
  THRESHOLD_PERCENT  - a number (e.g. "10"), or blank/unset to clear the
                        threshold and go back to "alert on any change"

Meaning of the threshold: check_prices.py will only email about this item
when the price is at or within THRESHOLD_PERCENT of the lowest price ever
recorded for it (a "this is basically the best price ever" alert),
instead of the default "alert on any change" behavior.

Writes GITHUB_OUTPUT keys `status` (ok|error) and `message`.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "products.json"

MIN_PERCENT = 0
MAX_PERCENT = 100


def normalize_url(url):
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def write_output(status, message):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"status={status}\n")
            f.write(f"message={message}\n")
    print(f"[{status}] {message}")


def main():
    link = os.environ.get("PRODUCT_LINK", "").strip()
    percent_raw = os.environ.get("THRESHOLD_PERCENT", "").strip()

    if not link:
        write_output("error", "No product link was provided.")
        return 1

    norm_url = normalize_url(link)

    if not PRODUCTS_FILE.exists():
        write_output("error", "products.json not found.")
        return 1

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        config = json.loads(content) if content else {}

    config.setdefault("thresholds", {})

    if percent_raw == "":
        if norm_url in config["thresholds"]:
            del config["thresholds"][norm_url]
            write_output("ok", f"Cleared threshold for {norm_url} - back to alerting on any price change.")
        else:
            write_output("ok", f"No threshold was set for {norm_url} - nothing to clear.")
    else:
        try:
            percent = float(percent_raw)
        except ValueError:
            write_output("error", f"'{percent_raw}' isn't a valid number.")
            return 1

        if not (MIN_PERCENT <= percent <= MAX_PERCENT):
            write_output("error", f"Threshold must be between {MIN_PERCENT} and {MAX_PERCENT}.")
            return 1

        config["thresholds"][norm_url] = percent
        write_output("ok", f"Set threshold for {norm_url} to {percent}% above its lowest recorded price.")

    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

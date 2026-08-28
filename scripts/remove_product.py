#!/usr/bin/env python3
"""
Removes a tracked product or wishlist from products.json - e.g. after
you've bought it and no longer want it checked. Also drops any per-item
threshold override for it, and its entry in data/latest.json so it stops
showing on the price table immediately (data/history.json is left alone -
harmless to keep around, and it means re-adding the same link later
picks up its old price history instead of starting over).

Note: for a wishlist-derived item (not a directly-added product link),
this only stops it from showing on the table right now - if it's still
on the actual Amazon wishlist, the next successful check will find it
there again and re-add it. Direct product links don't have this issue.

Run by .github/workflows/remove-product.yml, triggered from the price
table page's "Mark purchased" button (or manually via "Run workflow").

Writes GITHUB_OUTPUT keys `status` (ok|error), `message`, and `name`.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "products.json"
LATEST_FILE = ROOT / "data" / "latest.json"


def normalize_url(url):
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def write_output(status, message, name=""):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"status={status}\n")
            f.write(f"message={message}\n")
            f.write(f"name={name}\n")
    print(f"[{status}] {message}")


def main():
    link = os.environ.get("PRODUCT_LINK", "").strip()
    if not link:
        write_output("error", "No link was given to remove.")
        return 0

    norm_url = normalize_url(link)

    config = load_json(PRODUCTS_FILE, {"wishlists": [], "products": []})
    config.setdefault("wishlists", [])
    config.setdefault("products", [])
    config.setdefault("thresholds", {})

    products = config["products"]
    removed_name = None
    for p in products:
        url = p["url"] if isinstance(p, dict) else p
        if url == norm_url:
            removed_name = p.get("name", url) if isinstance(p, dict) else url
            break

    remaining = [p for p in products if (p["url"] if isinstance(p, dict) else p) != norm_url]
    removed = len(remaining) != len(products)
    config["products"] = remaining

    if not removed and norm_url in config["wishlists"]:
        config["wishlists"].remove(norm_url)
        removed = True
        removed_name = "Wishlist"

    if not removed:
        write_output("error", f"Couldn't find '{norm_url}' in the tracked list - it may already be removed.")
        return 0

    config["thresholds"].pop(norm_url, None)
    save_json(PRODUCTS_FILE, config)

    latest = load_json(LATEST_FILE, {})
    if norm_url in latest:
        del latest[norm_url]
        save_json(LATEST_FILE, latest)

    write_output("ok", f"Removed '{removed_name}' from tracking", removed_name or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())

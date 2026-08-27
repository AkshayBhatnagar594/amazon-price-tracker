#!/usr/bin/env python3
"""
Parses the body of a GitHub Issue opened via the "Add a product to track"
issue form (.github/ISSUE_TEMPLATE/add-product.yml) and appends the link
to products.json.

Run by .github/workflows/add-product.yml whenever a new issue with the
"add-product" label is opened. Expects the issue body in the ISSUE_BODY
environment variable.

Writes GITHUB_OUTPUT keys `status` (ok|error), `message` (human-readable
result), and `name` (the product/wishlist name) so the workflow can post a
comment back on the issue.
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "products.json"

WISHLIST_PATTERN = re.compile(r"/hz/wishlist/|/registry/wishlist/")
AMAZON_HOST_PATTERN = re.compile(r"amazon\.[a-z.]+$", re.IGNORECASE)
ASIN_PATTERN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_NAME_LENGTH = 60


def shorten(text, max_len=MAX_NAME_LENGTH):
    text = text.strip()
    if len(text) <= max_len:
        return text
    # cut at the last full word that fits, so we don't chop mid-word
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return (truncated or text[:max_len]).rstrip(",.-") + "..."


def guess_name_from_url(url):
    match = ASIN_PATTERN.search(url)
    return f"Amazon item {match.group(1)}" if match else "Amazon item"


def fetch_product_name(url):
    """
    Best-effort fetch of the product title straight from the Amazon page.
    Falls back to an ASIN-based placeholder name if the request fails or
    Amazon serves a bot-check page (common from shared CI IPs) - never
    raises, since a missing name shouldn't block adding the product.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        title_tag = soup.select_one("#productTitle")
        if title_tag:
            title = title_tag.get_text(strip=True)
            if title:
                return shorten(title)
    except requests.RequestException:
        pass
    return guess_name_from_url(url)


def parse_issue_form(body):
    """
    GitHub renders each issue-form field as:
        ### <field label>

        <value or '_No response_'>

    Returns a dict of {normalized_field_label: value}.
    """
    fields = {}
    sections = re.split(r"^### (.+)$", body or "", flags=re.MULTILINE)
    # sections looks like ['', 'label1', 'value1', 'label2', 'value2', ...]
    for i in range(1, len(sections), 2):
        label = sections[i].strip().lower()
        value = sections[i + 1].strip() if i + 1 < len(sections) else ""
        if value == "_No response_":
            value = ""
        fields[label] = value
    return fields


def normalize_url(url):
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def write_output(status, message, name=""):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"status={status}\n")
            f.write(f"message={message}\n")
            f.write(f"name={name}\n")
    print(f"[{status}] {message}")


def main():
    body = os.environ.get("ISSUE_BODY", "")
    fields = parse_issue_form(body)

    link = fields.get("amazon product link") or fields.get("amazon link") or ""
    nickname = fields.get("nickname (optional)") or fields.get("nickname") or ""

    if not link:
        write_output("error", "No Amazon link was found in the issue body. Please use the 'Add a product to track' issue form.")
        return 1

    if not AMAZON_HOST_PATTERN.search(urlparse(link if "://" in link else "https://" + link).netloc):
        write_output("error", f"'{link}' doesn't look like an amazon.* URL. Please double-check the link and reopen the issue.")
        return 1

    norm_url = normalize_url(link)
    is_wishlist = bool(WISHLIST_PATTERN.search(norm_url))

    if not PRODUCTS_FILE.exists():
        config = {"wishlists": [], "products": []}
    else:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            config = json.loads(content) if content else {"wishlists": [], "products": []}

    config.setdefault("wishlists", [])
    config.setdefault("products", [])

    if is_wishlist:
        if norm_url in config["wishlists"]:
            write_output("error", f"That wishlist is already being tracked: {norm_url}")
            return 1
        config["wishlists"].append(norm_url)
        write_output("ok", f"Added wishlist to tracking: {norm_url}", nickname or "Wishlist")
    else:
        existing_urls = {p["url"] if isinstance(p, dict) else p for p in config["products"]}
        if norm_url in existing_urls:
            write_output("error", f"That product is already being tracked: {norm_url}")
            return 1
        name = nickname or fetch_product_name(norm_url)
        entry = {"url": norm_url, "name": name}
        config["products"].append(entry)
        write_output("ok", f"Added '{name}' to tracking", name)

    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

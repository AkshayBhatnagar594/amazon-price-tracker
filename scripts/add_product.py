#!/usr/bin/env python3
"""
Adds an Amazon product/wishlist link to products.json. The link can come
from two places, checked in this order:

1. The PRODUCT_LINK environment variable - set when the workflow is run
   manually via "Run workflow" (workflow_dispatch), which only asks for
   the link itself, no issue/title involved.
2. The ISSUE_BODY environment variable - the body of a GitHub Issue
   opened via the "Add a product to track" issue form
   (.github/ISSUE_TEMPLATE/add-product.yml). Issues always require a
   title (a GitHub platform requirement, can't be turned off), but
   nothing reads it - the product's name comes from the page itself.

When adding a direct product link, this also makes a best-effort attempt to
fetch its current price right away (with a couple of retries), so the price
table shows real data immediately instead of waiting for the next scheduled
check_prices.py run - useful now that scheduled runs may only happen once a
day. If that fetch gets blocked (Amazon's bot-check), the product is still
added; it'll just show "not checked yet" until a check_prices.py run gets
through.

Run by .github/workflows/add-product.yml.

Writes GITHUB_OUTPUT keys `status` (ok|error), `message` (human-readable
result), and `name` (the product/wishlist name).
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "data" / "history.json"
LATEST_FILE = ROOT / "data" / "latest.json"

WISHLIST_PATTERN = re.compile(r"/hz/wishlist/|/registry/wishlist/")
AMAZON_HOST_PATTERN = re.compile(r"amazon\.[a-z.]+$", re.IGNORECASE)
ASIN_PATTERN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)
# Amazon's own link shorteners (e.g. shared from the mobile app / Share sheet).
# These don't point at an amazon.* host directly - they redirect to one, so
# they need to be resolved before the normal host check can pass.
SHORTLINK_HOST_PATTERN = re.compile(r"^(a\.co|amzn\.to|amzn\.eu|amzn\.in|amzn\.asia)$", re.IGNORECASE)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_NAME_LENGTH = 60

# Same selectors/logic check_prices.py uses to read a price off the page.
PRICE_SELECTORS = [
    "span.a-price span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#corePrice_feature_div span.a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.a-offscreen",
]
FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 5


def shorten(text, max_len=MAX_NAME_LENGTH):
    text = text.strip()
    if len(text) <= max_len:
        return text
    # cut at the last full word that fits, so we don't chop mid-word
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return (truncated or text[:max_len]).rstrip(",.-") + "..."


SLUG_PATTERN = re.compile(r"/([A-Za-z0-9][A-Za-z0-9-]{9,})/(?:dp|gp/product)/", re.IGNORECASE)
GENERIC_SLUG_WORDS = {"dp", "gp", "product", "the", "and"}


def guess_name_from_url(url):
    """
    Best-effort name when the live page fetch didn't work. Amazon URLs
    often carry a readable product slug right before /dp/, e.g.
    amazon.com/Sony-WH-1000XM4-Wireless-Headphones/dp/B0863TXGM3 - use
    that if present, since it's usually far more useful than a bare ASIN.
    """
    slug_match = SLUG_PATTERN.search(url)
    if slug_match:
        words = [w for w in slug_match.group(1).split("-") if w.lower() not in GENERIC_SLUG_WORDS]
        if words:
            return shorten(" ".join(words))

    asin_match = ASIN_PATTERN.search(url)
    return f"Amazon item {asin_match.group(1)}" if asin_match else "Amazon item"


def resolve_shortlink(url):
    """
    Follows redirects on an Amazon shortlink (a.co, amzn.to, ...) to get the
    real amazon.* product URL. Returns the resolved URL, or None if it
    couldn't be resolved.
    """
    try:
        resp = requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.url and resp.url != url:
            return resp.url
    except requests.RequestException:
        pass
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.url and resp.url != url:
            return resp.url
    except requests.RequestException:
        pass
    return None


def parse_price(text):
    if not text:
        return None
    match = re.search(r"[\d,]+\.\d{2}|[\d,]+", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def looks_like_captcha(soup):
    text = soup.get_text(" ", strip=True).lower()
    return "enter the characters you see below" in text or "api-services-support@amazon.com" in text


def fetch_product_details(url):
    """
    Best-effort live fetch of both the product title and its current price
    in one request. Amazon's bot-check is inconsistent run to run, so this
    retries a few times with a short delay before giving up. Never raises -
    returns (name, price) with either/both as None on failure, since a
    fetch problem should never block adding the product itself (the name
    falls back to guess_name_from_url, and the price just stays unset until
    a later check_prices.py run gets through).
    """
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        soup = None
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
        except requests.RequestException:
            soup = None

        if soup is not None and not looks_like_captcha(soup):
            title_tag = soup.select_one("#productTitle")
            title_text = title_tag.get_text(strip=True) if title_tag else ""
            name = shorten(title_text) if title_text else None

            price = None
            for selector in PRICE_SELECTORS:
                tag = soup.select_one(selector)
                if tag:
                    price = parse_price(tag.get_text(strip=True))
                    if price is not None:
                        break

            if name or price is not None:
                return name, price

        if attempt < FETCH_ATTEMPTS:
            time.sleep(FETCH_RETRY_DELAY_SECONDS)

    return None, None


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
    direct_link = os.environ.get("PRODUCT_LINK", "").strip()

    if direct_link:
        link = direct_link
        nickname = os.environ.get("PRODUCT_NICKNAME", "").strip()
    else:
        body = os.environ.get("ISSUE_BODY", "")
        fields = parse_issue_form(body)
        link = fields.get("amazon product link") or fields.get("amazon link") or ""
        nickname = fields.get("nickname (optional)") or fields.get("nickname") or ""

    if not link:
        write_output("error", "No Amazon link was found in the issue body. Please use the 'Add a product to track' issue form.")
        return 0

    netloc = urlparse(link if "://" in link else "https://" + link).netloc

    if SHORTLINK_HOST_PATTERN.match(netloc):
        resolved = resolve_shortlink(link if "://" in link else "https://" + link)
        if not resolved or not AMAZON_HOST_PATTERN.search(urlparse(resolved).netloc):
            write_output("error", f"Couldn't resolve the shortlink '{link}' to an amazon.* product page. Please paste the full amazon.com link instead (open the link in a browser, then copy the address bar URL).")
            return 0
        link = resolved
        netloc = urlparse(link).netloc

    if not AMAZON_HOST_PATTERN.search(netloc):
        write_output("error", f"'{link}' doesn't look like an amazon.* URL. Please double-check the link and reopen the issue.")
        return 0

    norm_url = normalize_url(link)
    is_wishlist = bool(WISHLIST_PATTERN.search(norm_url))

    config = load_json(PRODUCTS_FILE, {"wishlists": [], "products": []})
    config.setdefault("wishlists", [])
    config.setdefault("products", [])

    if is_wishlist:
        if norm_url in config["wishlists"]:
            write_output("error", f"That wishlist is already being tracked: {norm_url}")
            return 0
        config["wishlists"].append(norm_url)
        write_output("ok", f"Added wishlist to tracking: {norm_url}", nickname or "Wishlist")
    else:
        existing_urls = {p["url"] if isinstance(p, dict) else p for p in config["products"]}
        if norm_url in existing_urls:
            write_output("error", f"That product is already being tracked: {norm_url}")
            return 0

        fetched_name, price = fetch_product_details(norm_url)
        name = nickname or fetched_name or guess_name_from_url(norm_url)
        entry = {"url": norm_url, "name": name}
        config["products"].append(entry)

        price_note = ""
        if price is not None:
            now = datetime.now(timezone.utc).isoformat()
            history = load_json(HISTORY_FILE, {})
            latest = load_json(LATEST_FILE, {})
            history.setdefault(norm_url, []).append({"date": now, "price": price})
            latest[norm_url] = {"name": name, "price": price, "last_checked": now}
            save_json(HISTORY_FILE, history)
            save_json(LATEST_FILE, latest)
            price_note = f" at ${price:.2f}"

        write_output("ok", f"Added '{name}'{price_note} to tracking", name)

    save_json(PRODUCTS_FILE, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())

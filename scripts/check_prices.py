#!/usr/bin/env python3
"""
Checks the current Amazon price for every product/wishlist configured in
products.json, records it in data/history.json + data/latest.json, and
emails a summary when any price has changed since the last check.

Run manually with:  python scripts/check_prices.py
Run automatically by .github/workflows/check-prices.yml on a schedule.
"""

import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "data" / "history.json"
LATEST_FILE = ROOT / "data" / "latest.json"
LAST_RUN_FILE = ROOT / "data" / "last_run.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 3  # be polite between requests

PRICE_SELECTORS = [
    "span.a-price span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#corePrice_feature_div span.a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.a-offscreen",
]


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return default
        return json.loads(content)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def normalize_url(url):
    """Strip tracking query params so the same product isn't tracked twice."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


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


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def check_product(url, fallback_name=None):
    """Returns (name, price, status, detail). status is one of:
    ok | request_error | captcha | no_price_found
    """
    url = normalize_url(url)
    try:
        soup = fetch(url)
    except requests.RequestException as e:
        print(f"  [warn] request failed for {url}: {e}")
        return fallback_name or url, None, "request_error", str(e)[:300]

    if looks_like_captcha(soup):
        print(f"  [warn] Amazon returned a CAPTCHA/robot check for {url} - skipping this run")
        return fallback_name or url, None, "captcha", "Amazon served a robot-check page instead of the product page"

    title_tag = soup.select_one("#productTitle")
    name = title_tag.get_text(strip=True) if title_tag else (fallback_name or url)

    price = None
    for selector in PRICE_SELECTORS:
        tag = soup.select_one(selector)
        if tag:
            price = parse_price(tag.get_text(strip=True))
            if price is not None:
                break

    if price is None:
        detail = f"none of the known price selectors matched (page title: {soup.title.get_text(strip=True) if soup.title else 'n/a'})"
        print(f"  [warn] could not find a price for {url} (Amazon may have changed their page layout)")
        return name, None, "no_price_found", detail[:300]

    return name, price, "ok", ""


def check_wishlist(url):
    """
    Returns (items, status, detail). items is a list of
    (name, url, price) tuples for items visible on the first page of a
    public Amazon wishlist. Amazon loads additional items via JavaScript
    as you scroll, so only the items on the initial page load are picked
    up here - see README for details.
    """
    try:
        soup = fetch(url)
    except requests.RequestException as e:
        print(f"  [warn] request failed for wishlist {url}: {e}")
        return [], "request_error", str(e)[:300]

    if looks_like_captcha(soup):
        print(f"  [warn] Amazon returned a CAPTCHA/robot check for wishlist {url} - skipping this run")
        return [], "captcha", "Amazon served a robot-check page instead of the wishlist page"

    results = []
    items = soup.select("li[data-itemid]")
    for item in items:
        item_id = item.get("data-itemid")
        name_tag = item.select_one(f"#itemName_{item_id}") or item.select_one("[id^='itemName_']")
        price_tag = item.select_one(f"#itemPrice_{item_id}") or item.select_one("[id^='itemPrice_']")

        if not name_tag:
            continue

        name = name_tag.get_text(strip=True) or name_tag.get("title", "").strip()
        href = name_tag.get("href")
        if not href:
            continue
        product_url = normalize_url(urljoin("https://www.amazon.com", href))

        price_text = price_tag.get_text(strip=True) if price_tag else None
        price = parse_price(price_text)

        results.append((name or product_url, product_url, price))

    if not items:
        detail = f"no items found on wishlist page - it may be private, empty, or Amazon changed their layout (page title: {soup.title.get_text(strip=True) if soup.title else 'n/a'})"
        print(f"  [warn] {detail}")
        return results, "no_items_found", detail[:300]

    return results, "ok", ""


def send_email(changes):
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    email_to = os.environ.get("EMAIL_TO")

    if not all([smtp_username, smtp_password, email_to]):
        print("  [warn] SMTP_USERNAME, SMTP_PASSWORD, or EMAIL_TO not set - skipping email, see README for setup")
        return

    lines = []
    for c in changes:
        direction = "UP" if c["new_price"] > (c["old_price"] or 0) else "DOWN"
        old = f"${c['old_price']:.2f}" if c["old_price"] is not None else "unknown"
        new = f"${c['new_price']:.2f}"
        lines.append(f"[{direction}] {c['name']}\n  {old} -> {new}\n  {c['url']}\n")

    body = "Price changes detected:\n\n" + "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = smtp_username
    msg["To"] = email_to
    msg["Subject"] = f"Amazon price change alert ({len(changes)} item{'s' if len(changes) != 1 else ''})"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, email_to, msg.as_string())
        print(f"  [ok] email sent to {email_to}")
    except smtplib.SMTPException as e:
        print(f"  [error] failed to send email: {e}")


def main():
    products_config = load_json(PRODUCTS_FILE, {"wishlists": [], "products": []})
    history = load_json(HISTORY_FILE, {})
    latest = load_json(LATEST_FILE, {})

    now = datetime.now(timezone.utc).isoformat()
    changes = []
    run_log = []

    # Direct product links
    for entry in products_config.get("products", []):
        url = entry["url"] if isinstance(entry, dict) else entry
        fallback_name = entry.get("name") if isinstance(entry, dict) else None
        norm_url = normalize_url(url)

        print(f"Checking product: {fallback_name or norm_url}")
        name, price, status, detail = check_product(url, fallback_name)
        time.sleep(REQUEST_DELAY_SECONDS)

        run_log.append({"type": "product", "url": norm_url, "name": name, "status": status, "detail": detail, "price": price})

        if price is None:
            continue

        old_price = latest.get(norm_url, {}).get("price")
        if old_price is not None and old_price != price:
            changes.append({"name": name, "url": norm_url, "old_price": old_price, "new_price": price})

        history.setdefault(norm_url, []).append({"date": now, "price": price})
        latest[norm_url] = {"name": name, "price": price, "last_checked": now}

    # Wishlists
    for wishlist_url in products_config.get("wishlists", []):
        print(f"Checking wishlist: {wishlist_url}")
        items, wl_status, wl_detail = check_wishlist(wishlist_url)
        time.sleep(REQUEST_DELAY_SECONDS)

        run_log.append({"type": "wishlist", "url": wishlist_url, "status": wl_status, "detail": wl_detail, "item_count": len(items)})

        for name, product_url, price in items:
            run_log.append({"type": "wishlist_item", "url": product_url, "name": name, "status": "ok" if price is not None else "no_price_found", "detail": "", "price": price})

            if price is None:
                continue

            old_price = latest.get(product_url, {}).get("price")
            if old_price is not None and old_price != price:
                changes.append({"name": name, "url": product_url, "old_price": old_price, "new_price": price})

            history.setdefault(product_url, []).append({"date": now, "price": price})
            latest[product_url] = {"name": name, "price": price, "last_checked": now}

    save_json(HISTORY_FILE, history)
    save_json(LATEST_FILE, latest)
    save_json(LAST_RUN_FILE, {"timestamp": now, "results": run_log})

    if changes:
        print(f"\n{len(changes)} price change(s) detected:")
        for c in changes:
            print(f"  {c['name']}: {c['old_price']} -> {c['new_price']}")
        send_email(changes)
    else:
        print("\nNo price changes detected.")


if __name__ == "__main__":
    sys.exit(main())

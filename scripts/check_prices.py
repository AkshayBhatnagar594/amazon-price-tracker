#!/usr/bin/env python3
"""
Checks the current Amazon price for every product/wishlist configured in
products.json, records it in data/history.json + data/latest.json, and
sends a push notification (via ntfy.sh) when a price is worth knowing
about.

"Worth knowing about" - see should_alert() - is judged against the lowest
price *we've* recorded for that item, in data/history.json. There's no
free, reliable way to get Amazon's own official price history (Amazon
doesn't publish one, and third-party sites that track it, like
camelcamelcamel, block scraping from cloud IPs same as Amazon does) - so
this baseline starts at "lowest since you added it" and becomes more
meaningful the longer an item's been tracked, rather than a true 365-day
low from day one.

Notifications are de-duped using data/alert_state.json: once an item
enters its "near the low" band, you're notified once, not again on every
run while it stays there - only if it drops even lower, or leaves the
band and dips back into it later.

Run manually with:  python scripts/check_prices.py
Run automatically by .github/workflows/check-prices.yml on a schedule.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "data" / "history.json"
LATEST_FILE = ROOT / "data" / "latest.json"
LAST_RUN_FILE = ROOT / "data" / "last_run.json"
ALERT_STATE_FILE = ROOT / "data" / "alert_state.json"

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
DEFAULT_CHECK_INTERVAL_HOURS = 6
DEFAULT_ALERT_THRESHOLD_PERCENT = 25

PRICE_SELECTORS = [
    "span.a-price span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#corePrice_feature_div span.a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.a-offscreen",
]

# Amazon's crossed-out "list price" / "was" price, when it shows one -
# distinct from the current offer price above. Not every product has one.
ORIGINAL_PRICE_SELECTORS = [
    "span.a-price.a-text-price span.a-offscreen",
    "span[data-a-strike='true'] span.a-offscreen",
    ".basisPrice .a-offscreen",
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


def extract_original_price(soup):
    for selector in ORIGINAL_PRICE_SELECTORS:
        tag = soup.select_one(selector)
        if tag:
            price = parse_price(tag.get_text(strip=True))
            if price is not None:
                return price
    return None


def check_product(url, fallback_name=None):
    """Returns (name, price, original_price, status, detail). status is
    one of: ok | request_error | captcha | no_price_found. original_price
    is Amazon's own crossed-out "list price" if the page shows one, else
    None - it's None whenever price is also None.
    """
    url = normalize_url(url)
    try:
        soup = fetch(url)
    except requests.RequestException as e:
        print(f"  [warn] request failed for {url}: {e}")
        return fallback_name or url, None, None, "request_error", str(e)[:300]

    if looks_like_captcha(soup):
        print(f"  [warn] Amazon returned a CAPTCHA/robot check for {url} - skipping this run")
        return fallback_name or url, None, None, "captcha", "Amazon served a robot-check page instead of the product page"

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
        snippet = soup.get_text(" ", strip=True)[:300]
        title = soup.title.get_text(strip=True) if soup.title else "n/a"
        detail = f"page title: {title} | body snippet: {snippet}"
        print(f"  [warn] could not find a price for {url} (Amazon may have changed their page layout)")
        return name, None, None, "no_price_found", detail[:500]

    original_price = extract_original_price(soup)
    return name, price, original_price, "ok", ""


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


def send_ntfy(changes):
    """
    Posts a single push notification (via ntfy.sh, or a self-hosted ntfy
    server if NTFY_SERVER is set) summarizing every price worth knowing
    about from this run. Requires NTFY_TOPIC - see README for setup.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("  [warn] NTFY_TOPIC not set - skipping notification, see README for setup")
        return

    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    lines = []
    for c in changes:
        new = f"${c['new_price']:.2f}"
        if c.get("lowest_ever") is not None and c["new_price"] <= c["lowest_ever"]:
            note = "new low!"
        elif c.get("lowest_ever") is not None:
            note = f"near the low of ${c['lowest_ever']:.2f}"
        else:
            old = f"${c['old_price']:.2f}" if c["old_price"] is not None else "unknown"
            note = f"was {old}"
        lines.append(f"{c['name']} - {new} ({note})\n{c['url']}")

    body = "\n\n".join(lines)
    title = f"Amazon price alert ({len(changes)} item{'s' if len(changes) != 1 else ''})"

    try:
        resp = requests.post(
            f"{server}/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "default",
                "Tags": "moneybag",
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"  [error] ntfy responded with {resp.status_code}: {resp.text[:200]}")
        else:
            print(f"  [ok] notification sent to ntfy topic '{topic}'")
    except requests.RequestException as e:
        print(f"  [error] failed to send ntfy notification: {e}")


def should_alert(price, old_price, prior_history, threshold_percent):
    """
    Decides whether a price is worth notifying about.

    - No prior price at all -> never (this is the first successful check
      for this item, there's nothing to compare against yet).
    - threshold_percent is None -> alert on any change from the
      immediately previous check. Callers resolve this from a per-item
      override in products.json's "thresholds", falling back to
      "default_alert_threshold_percent" (25 unless set otherwise) - so in
      practice this None branch only fires if someone explicitly sets a
      threshold to null for one item to opt it out of the default.
    - threshold_percent is a number -> alert only when the new price is at
      or within threshold_percent of the lowest price ever recorded for
      this item ("this is basically the best price ever" deal alert),
      instead of on every small fluctuation.

    Returns (should_alert, lowest_ever_or_None).
    """
    if old_price is None:
        return False, None

    if threshold_percent is None:
        return old_price != price, None

    prior_prices = [h["price"] for h in prior_history if h.get("price") is not None]
    if not prior_prices:
        return False, None

    lowest_ever = min(prior_prices)
    return price <= lowest_ever * (1 + threshold_percent / 100), lowest_ever


def evaluate_alert(url, price, old_price, prior_history, threshold_percent, alert_state):
    """
    Wraps should_alert() with de-dup state so a price that's still near
    the low doesn't notify again on every run. alert_state is mutated in
    place: url -> {"price": ..., "date": ...} while it's in the "near the
    low" band, removed once it leaves the band (so a later dip back in
    notifies again fresh).

    Returns (should_notify, lowest_ever_or_None).
    """
    eligible, lowest_ever = should_alert(price, old_price, prior_history, threshold_percent)
    if not eligible:
        alert_state.pop(url, None)
        return False, lowest_ever

    prev = alert_state.get(url)
    if prev is None or price < prev.get("price", float("inf")):
        alert_state[url] = {"price": price, "date": datetime.now(timezone.utc).isoformat()}
        return True, lowest_ever

    return False, lowest_ever


def should_run_now(products_config):
    """
    The workflow itself polls hourly, but the *effective* check interval
    is controlled by "check_interval_hours" in products.json (default 6)
    so it can be changed just by editing a number and pushing - no cron
    syntax, and it takes effect on the very next hourly poll instead of
    needing a workflow file change.
    """
    interval_hours = products_config.get("check_interval_hours", DEFAULT_CHECK_INTERVAL_HOURS)
    try:
        interval_hours = float(interval_hours)
    except (TypeError, ValueError):
        interval_hours = DEFAULT_CHECK_INTERVAL_HOURS

    last_run = load_json(LAST_RUN_FILE, {})
    last_timestamp = last_run.get("timestamp")
    if not last_timestamp:
        return True, interval_hours

    try:
        last_dt = datetime.fromisoformat(last_timestamp)
    except ValueError:
        return True, interval_hours

    elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return elapsed_hours >= interval_hours, interval_hours


def main():
    products_config = load_json(PRODUCTS_FILE, {"wishlists": [], "products": []})

    forced = os.environ.get("FORCE_CHECK", "").lower() == "true"
    run_now, interval_hours = should_run_now(products_config)
    if not run_now and not forced:
        print(f"Skipping - check_interval_hours is {interval_hours}, last check was more recent than that.")
        print("Edit \"check_interval_hours\" in products.json to change this, or run this workflow manually to check immediately regardless of the interval.")
        return
    if forced and not run_now:
        print("FORCE_CHECK is set (manual run) - checking now even though the interval hasn't elapsed.")

    history = load_json(HISTORY_FILE, {})
    latest = load_json(LATEST_FILE, {})
    alert_state = load_json(ALERT_STATE_FILE, {})
    thresholds = products_config.get("thresholds", {})
    # Per-item entries in "thresholds" override this; an item with no entry
    # there falls back to this default (25% unless changed in products.json).
    # Set "default_alert_threshold_percent": null in products.json to make
    # the default "alert on any change" instead.
    default_threshold = products_config.get("default_alert_threshold_percent", DEFAULT_ALERT_THRESHOLD_PERCENT)

    now = datetime.now(timezone.utc).isoformat()
    changes = []
    run_log = []
    products_changed = False

    # Direct product links
    products_list = products_config.get("products", [])
    for i, entry in enumerate(products_list):
        url = entry["url"] if isinstance(entry, dict) else entry
        fallback_name = entry.get("name") if isinstance(entry, dict) else None
        norm_url = normalize_url(url)

        print(f"Checking product: {fallback_name or norm_url}")
        name, price, original_price, status, detail = check_product(url, fallback_name)
        time.sleep(REQUEST_DELAY_SECONDS)

        run_log.append({"type": "product", "url": norm_url, "name": name, "status": status, "detail": detail, "price": price})

        if price is None:
            continue

        # Backfill "price when added" the first time we successfully see
        # Amazon's own list/"was" price for this item - add_product.py
        # tries this at add time, but if that attempt got blocked (common),
        # this picks it up later instead of leaving it permanently unset.
        if original_price is not None:
            if isinstance(entry, dict):
                if entry.get("original_price") is None:
                    entry["original_price"] = original_price
                    products_changed = True
            else:
                products_list[i] = {"url": url, "name": fallback_name or name, "original_price": original_price}
                products_changed = True

        old_price = latest.get(norm_url, {}).get("price")
        notify, lowest_ever = evaluate_alert(norm_url, price, old_price, history.get(norm_url, []), thresholds.get(norm_url, default_threshold), alert_state)
        if notify:
            changes.append({"name": name, "url": norm_url, "old_price": old_price, "new_price": price, "lowest_ever": lowest_ever})

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
            notify, lowest_ever = evaluate_alert(product_url, price, old_price, history.get(product_url, []), thresholds.get(product_url, default_threshold), alert_state)
            if notify:
                changes.append({"name": name, "url": product_url, "old_price": old_price, "new_price": price, "lowest_ever": lowest_ever})

            history.setdefault(product_url, []).append({"date": now, "price": price})
            latest[product_url] = {"name": name, "price": price, "last_checked": now}

    save_json(HISTORY_FILE, history)
    save_json(LATEST_FILE, latest)
    save_json(LAST_RUN_FILE, {"timestamp": now, "results": run_log})
    save_json(ALERT_STATE_FILE, alert_state)
    if products_changed:
        save_json(PRODUCTS_FILE, products_config)

    if changes:
        print(f"\n{len(changes)} price change(s) worth notifying about:")
        for c in changes:
            print(f"  {c['name']}: {c['old_price']} -> {c['new_price']}")
        send_ntfy(changes)
    else:
        print("\nNo price changes worth notifying about.")


if __name__ == "__main__":
    sys.exit(main())

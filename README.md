# Amazon Price Tracker

Tracks the price of Amazon products (or everything on a public Amazon
wishlist) and pushes a notification to your phone when a price is worth
knowing about. Runs entirely on GitHub Actions — no server to host.

- **Checks prices every 6 hours** via a scheduled GitHub Actions workflow.
- **A live web page** (GitHub Pages) lists everything tracked, with buttons
  to check prices right now, add a new link, and set a per-item price-drop
  alert threshold — all without leaving the page. See "The price table
  page" below.
- **Shows Amazon's own list price alongside the current one** — the
  "Original (when added)" column — so you can tell at a glance whether an
  item was already discounted the day you started tracking it.
- **Add new items** through that page, a title-free GitHub Actions form, or
  a GitHub Issue form — whichever's convenient.
- **Pushes a notification to your phone** (via [ntfy.sh](https://ntfy.sh),
  free, no account needed) when a tracked item's price is near its
  all-time low - once per "deal," not again every day it stays there.
- **Keeps full price history** in `data/history.json`, committed back to
  the repo on every run.

## How it works

```
.github/workflows/check-prices.yml     -> runs scripts/check_prices.py hourly (see check_interval_hours)
.github/workflows/add-product.yml      -> runs scripts/add_product.py from an issue OR a manual "Run workflow"
.github/workflows/set-threshold.yml    -> runs scripts/set_threshold.py to set/clear a price-alert threshold
.github/workflows/remove-product.yml   -> runs scripts/remove_product.py to stop tracking something
.github/ISSUE_TEMPLATE/add-product.yml -> the issue form you can use to add links
index.html                             -> the live price table page (GitHub Pages)
products.json                          -> the list of things being tracked + settings
data/history.json                      -> every price ever recorded, per item
data/latest.json                       -> the most recent known price per item
data/last_run.json                     -> per-item diagnostics from the last check
data/alert_state.json                  -> tracks which items you've already been notified about
scripts/check_prices.py                -> scraper + ntfy notifier
scripts/add_product.py                 -> parses a new product/wishlist link into products.json, fetches its name+price
scripts/set_threshold.py               -> sets/clears a per-item alert threshold
scripts/remove_product.py              -> drops an item from products.json (e.g. once you've bought it)
```

## The price table page

Once GitHub Pages is enabled (see setup below), the table lives at
`https://<your-username>.github.io/<repo-name>/`. It's read-only by
default; connecting a token unlocks three things right from the page:

- **"Check prices now"** — triggers a real check immediately (bypasses
  `check_interval_hours`), waits for it to finish, and refreshes the table.
- **The add-link box** — paste an Amazon link, click Add. Same auto-naming
  as the other add methods, and it fetches the current price right away
  too (with a couple of retries), rather than waiting for the next
  scheduled check.
- **Per-item "Alert near low"** — type a percentage and click Set. See
  "Setting a price-drop threshold" below for what it means.
- **"Mark purchased"** — click once to arm it, click again to confirm,
  and it's dropped from the tracked list (no more checks, no more
  notifications for it). See "Marking something as purchased" below.

These need a GitHub token because the page is static (no backend) — click
**Connect** and follow the in-page instructions to create a
**fine-grained personal access token** scoped to just this repo with
**Actions: Read and write** permission (nothing else). It's stored only in
your browser's `localStorage`, never sent anywhere but GitHub's API
directly from your browser. Revoke it anytime at
[github.com/settings/tokens](https://github.com/settings/tokens) if you
ever want to.

## The "Original (when added)" column

This shows Amazon's own crossed-out list/"was" price — not something this
tool calculates, but whatever Amazon itself displayed as the pre-discount
price the first time it was successfully captured for that item. It's
captured once and then never changes, so a month from now you can look at
an item and tell whether it was already marked down when you added it, or
whether the current price is a genuinely new discount.

A few things worth knowing:

- **Not every product has one.** Plenty of Amazon listings don't show a
  list price at all — the column just shows "—" for those.
- **It's captured opportunistically, not guaranteed at add time.** Adding
  a product tries to fetch it immediately, but if that attempt gets
  blocked by Amazon's bot-check (common on GitHub-hosted runners), it's
  automatically backfilled the first time a later scheduled check gets
  through cleanly — you don't need to do anything.
- **It's a snapshot, not "the lowest we've seen."** If Amazon's list price
  itself changes later, this column won't update — that's intentional, so
  the "was this already a deal when I added it" comparison stays fixed to
  add time. Use the "Lowest (tracked)" column for the ongoing lowest price.
- The percentage badge next to it (e.g. "-36% off list") compares
  **today's price** to this original price, so it moves as the current
  price changes even though the original price itself doesn't.

## Setting a price-drop threshold

By default, every tracked item only alerts when its price is **at or
within 25% of the lowest price ever recorded for it** — a "this is
basically the best price ever" deal alert, not noise on every $0.01 move.
A brand new all-time low always alerts regardless of the percentage.

**"Lowest price ever recorded" means recorded by this tool, not by
Amazon.** Amazon doesn't publish its own price-history feed, and the
free third-party sites that track it (e.g. camelcamelcamel) block
scraping from cloud IPs the same way Amazon does - so there's no free
way to seed this with Amazon's true 365-day low from day one. It starts
as "lowest since you added it" and becomes a more meaningful "lowest in
X months" the longer an item's been tracked. (A paid API like
[Keepa](https://keepa.com/#!api) does have Amazon's real historical
data, if you ever want to swap that in instead.)

**You're only notified once per "deal," not every day it's still true.**
Once an item enters the near-the-low band, `data/alert_state.json` marks
it as already-notified; it stays quiet on later checks unless the price
drops even further, or it rises back out of the band and dips into it
again later.

Two ways to change the threshold itself:

- **Per item** — set a number (via the table page, or by editing
  `products.json`'s `thresholds` object directly) to override just that
  one item, e.g. `5` for a tighter "only really good deals" threshold on
  something specific. A blank value on the page removes the override and
  goes back to using the default.
- **The default itself** — edit `default_alert_threshold_percent` at the
  top of `products.json` (25 out of the box). Set it to `null` to make
  the *default* "alert on any change" instead of near-low — items with
  their own per-item override are unaffected either way.

```json
{
  "default_alert_threshold_percent": 25,
  "thresholds": {
    "https://www.amazon.com/dp/B0XXXXXXXX/": 5
  }
}
```

## Marking something as purchased

Bought it? Click **"Mark purchased"** next to that item on the table page
- click once to arm the button ("Confirm remove?"), click again within a
few seconds to confirm. That drops it from `products.json` (and its
threshold override, if it had one), so it stops being checked and stops
being able to notify you. Its price history in `data/history.json` is
left alone, so if you ever add the exact same link again later, it picks
up right where it left off instead of starting over.

One caveat: this only applies to items added as direct product links. If
an item came from a **wishlist** and it's still actually on that Amazon
wishlist, the next check will find it there again and re-add it - you'd
need to remove it from the Amazon wishlist itself too.

## Setup

1. **Create the repo.** See "Pushing this to GitHub" below if you're
   starting from these files locally.

2. **Set up push notifications with [ntfy.sh](https://ntfy.sh)** (free, no
   account needed):

   - Pick a **topic name** - this is like a private channel name. Make it
     hard to guess (e.g. `aj-amazon-tracker-x7q2`), since anyone who knows
     it can subscribe to your alerts on the public server.
   - Install the ntfy app ([iOS](https://apps.apple.com/us/app/ntfy/id1625396347),
     [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)),
     or use the [web app](https://ntfy.sh/app), and subscribe to that
     topic name.
   - Add a repository secret (Settings → Secrets and variables → Actions →
     New repository secret):

     | Secret | Required | Notes |
     |---|---|---|
     | `NTFY_TOPIC` | yes | The topic name you picked above |
     | `NTFY_SERVER` | no | Defaults to `https://ntfy.sh` - only set this if self-hosting ntfy |

   That's it - no password, no app-specific setup. Test it any time with
   `curl -d "test" ntfy.sh/your-topic-name`.

3. **Add products to track**, any of these ways:

   - **The price table page** — paste the link into the add box and click
     Add (needs a connected token — see "The price table page" below).
   - **Standalone link (no title field, just the link):** go to the
     [Actions tab → "Add product" → Run
     workflow](../../actions/workflows/add-product.yml), paste the Amazon
     link into the one field, and run it. Nothing else to fill in — the
     product's name is fetched from the page automatically. Bookmark that
     link for one-click adding later.
   - **Issue form (works well from your phone, e.g. the Share sheet):** go
     to the Issues tab → New Issue → "Add a product to track" → paste the
     Amazon link → Submit. Note GitHub issues always require a title field
     — that's a GitHub platform requirement with no way to turn off — but
     you can leave it as the pre-filled default, it's never read; the name
     comes from the product page instead. A bot files the link into
     `products.json`, renames the issue to match the product, comments to
     confirm, and closes it.
   - **Edit `products.json` directly** — useful for bulk-adding:

     ```json
     {
       "wishlists": ["https://www.amazon.com/hz/wishlist/ls/XXXXXXXXXXXXX"],
       "products": [
         { "url": "https://www.amazon.com/dp/B0XXXXXXXX", "name": "Headphones" }
       ]
     }
     ```

4. **Run it.** It runs automatically (every 6 hours by default — see
   "Changing the check frequency" below). To check right away: Actions tab
   → "Check prices" → Run workflow.

## Wishlist tracking — a heads-up

Point `wishlists` at a **public** Amazon wishlist link
(`amazon.com/hz/wishlist/ls/...`) and every item on it gets tracked
automatically, so you don't have to add things one at a time. The catch:
Amazon loads a wishlist's items via JavaScript as you scroll, and this
tracker does a plain HTTP fetch (no browser), so only the items visible on
the **first page load** (usually the first ~20-40) are picked up. For a
short list this just works; for a long one, some items further down won't
be seen. Individual product links added via the issue form don't have this
limitation.

## A note on scraping Amazon

Amazon doesn't offer a free public price API, so this reads prices off the
product page's HTML, the same way a browser would. That comes with two
caveats worth knowing:

- **It's against Amazon's Conditions of Use** to scrape their site
  programmatically. This project is intended for light, personal use
  (checking a handful of items every few hours) — not for anything at
  scale. Use it at your own discretion.
- **It's fragile.** If Amazon changes their page layout, the CSS selectors
  in `scripts/check_prices.py` (`PRICE_SELECTORS`) may need updating.
- **GitHub-hosted runners often get soft-blocked.** Amazon's anti-bot
  system flags most cloud datacenter IP ranges, GitHub Actions included.
  In practice this means a check run frequently gets served a generic
  "Continue shopping" interstitial or a CAPTCHA instead of the real page.
  The script detects this, logs it, and skips that item for the run
  rather than failing — check `data/last_run.json` after any run to see
  exactly what happened per item (`status`: `ok`, `captcha`,
  `no_price_found`, or `request_error`, plus a `detail` with a snippet of
  what Amazon actually returned). Because GitHub assigns a fresh runner
  (and IP) to each run, results can vary run to run — some checks will get
  through, some won't.

If this blocking makes it too unreliable for your needs, two ways to fix it:

- **Run it on a self-hosted runner** instead of GitHub's shared ones —
  e.g. a small always-on machine on your home network. A residential IP is
  far less likely to be flagged. See [GitHub's self-hosted runner
  docs](https://docs.github.com/actions/hosting-your-own-runners) and
  change `runs-on: ubuntu-latest` to `runs-on: self-hosted` in
  `.github/workflows/check-prices.yml`.
- **Use a paid API instead of scraping** — [Keepa](https://keepa.com/#!api)
  provides Amazon price history officially and could be swapped in for
  `check_product()`/`check_wishlist()`.

## Changing the check frequency

The "Check prices" workflow itself polls every hour, but it only actually
checks prices once `check_interval_hours` worth of time has passed since
the last real check — that number lives in `products.json`:

```json
{
  "check_interval_hours": 6,
  "wishlists": [],
  "products": [...]
}
```

Change it there (e.g. to `1` for hourly, `24` for daily, or `0.5` for
every 30 minutes), commit, and it takes effect on the very next hourly
poll — no cron syntax, no editing the workflow file. Every run where it
decides to skip is nearly instant and makes zero requests to Amazon, so
polling hourly costs nothing extra. If you want checks less often than
once an hour and don't want the extra no-op runs cluttering the Actions
tab, you can instead lower the polling cron itself in
`.github/workflows/check-prices.yml` (standard 5-field cron, evaluated in
UTC) — e.g. `"0 */6 * * *"` for every 6 hours.

## Pushing this to GitHub

If you have these files locally and the [GitHub CLI](https://cli.github.com/)
installed:

```bash
cd amazon-price-tracker
git init
git add .
git commit -m "Initial commit: Amazon price tracker"
gh repo create amazon-price-tracker --private --source=. --push
```

No `gh` CLI? Create an empty repo at https://github.com/new (don't
initialize it with a README), then:

```bash
cd amazon-price-tracker
git init
git add .
git commit -m "Initial commit: Amazon price tracker"
git branch -M main
git remote add origin https://github.com/<your-username>/amazon-price-tracker.git
git push -u origin main
```

Then add the secrets from step 2 above in the new repo's Settings page.

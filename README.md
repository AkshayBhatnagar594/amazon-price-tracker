# Amazon Price Tracker

Tracks the price of Amazon products (or everything on a public Amazon
wishlist) and emails you when a price changes. Runs entirely on GitHub
Actions — no server to host.

- **Checks prices every 6 hours** via a scheduled GitHub Actions workflow.
- **Add new items through a GitHub Issue form** — no need to edit code or
  files by hand. Open an issue, paste a link, submit; a bot files it into
  `products.json`, comments to confirm, and closes the issue.
- **Emails you** when a tracked item's price goes up or down.
- **Keeps full price history** in `data/history.json`, committed back to
  the repo on every run.

## How it works

```
.github/workflows/check-prices.yml   -> runs scripts/check_prices.py every 6h
.github/workflows/add-product.yml    -> runs scripts/add_product.py when you open an "add a product" issue
.github/ISSUE_TEMPLATE/add-product.yml -> the issue form ("dialog") you use to add links
products.json                        -> the list of things being tracked
data/history.json                    -> every price ever recorded, per item
data/latest.json                     -> the most recent known price per item
scripts/check_prices.py              -> scraper + email sender
scripts/add_product.py               -> parses a new-item issue into products.json
```

## Setup

1. **Create the repo.** See "Pushing this to GitHub" below if you're
   starting from these files locally.

2. **Add repository secrets** (Settings → Secrets and variables → Actions →
   New repository secret):

   | Secret | Required | Notes |
   |---|---|---|
   | `SMTP_USERNAME` | yes | Your sending email address, e.g. a Gmail address |
   | `SMTP_PASSWORD` | yes | An **app password**, not your normal password (see below) |
   | `EMAIL_TO` | yes | Where alerts should be sent — can be the same address |
   | `SMTP_SERVER` | no | Defaults to `smtp.gmail.com` |
   | `SMTP_PORT` | no | Defaults to `587` |

   If you use Gmail: enable 2-Step Verification on the Google account, then
   create an [App Password](https://myaccount.google.com/apppasswords) and
   use that as `SMTP_PASSWORD`. Any SMTP provider works (Outlook, Fastmail,
   SendGrid's SMTP relay, etc.) — just point `SMTP_SERVER`/`SMTP_PORT` at it.

3. **Add products to track**, any of these ways:

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

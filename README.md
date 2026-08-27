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

3. **Add products to track**, either way:

   - **Issue form (recommended, works from your phone too):** go to the
     Issues tab → New Issue → "Add a product to track" → paste the Amazon
     link → Submit. Within a minute or two a bot adds it to `products.json`,
     comments to confirm, and closes the issue.
   - **Edit `products.json` directly** — useful for bulk-adding:

     ```json
     {
       "wishlists": ["https://www.amazon.com/hz/wishlist/ls/XXXXXXXXXXXXX"],
       "products": [
         { "url": "https://www.amazon.com/dp/B0XXXXXXXX", "name": "Headphones" }
       ]
     }
     ```

4. **Run it.** It runs automatically every 6 hours. To check right away:
   Actions tab → "Check prices" → Run workflow.

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
  in `scripts/check_prices.py` (`PRICE_SELECTORS`) may need updating. If
  you check too frequently or from a flagged IP (GitHub's shared runners
  can occasionally get rate-limited), Amazon may serve a CAPTCHA page
  instead of the real one — the script detects this, logs a warning, and
  skips that item for the run rather than failing.

If you'd rather not scrape at all, a paid API like
[Keepa](https://keepa.com/#!api) provides Amazon price history officially
and could be swapped in for `check_product()`/`check_wishlist()`.

## Changing the check frequency

Edit the `cron` line in `.github/workflows/check-prices.yml`. It uses
standard cron syntax evaluated in UTC, e.g.:

- `"0 */6 * * *"` — every 6 hours (default)
- `"0 8,20 * * *"` — twice a day, 8am and 8pm UTC
- `"0 */1 * * *"` — every hour (more likely to get rate-limited — see above)

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

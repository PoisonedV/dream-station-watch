#!/usr/bin/env python3
"""Check Dream Station's public preorder catalog and notify Telegram of new listings.

Run as a periodic GitHub Action. No private store API or login required.
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from playwright.sync_api import sync_playwright

BASE = "https://dreamstation.com.sa"
CATEGORY = BASE + "/pre-orders/c630489014"
STATE_PATH = Path(__file__).with_name("state.json")

EXCLUSIVE_RE = re.compile(
    r"dream\s*station\s*(?:exclusive|special\s*edition|only|edition)"
    r"|(?:exclusive|only)\s*(?:at|for|to|from|by)?\s*dream\s*station"
    r"|(?:حصري|حصرية|حصريات|خاص(?:ة)?)\s*(?:لـ?|من|عند)?\s*دريم\s*(?:ستيشن|ستشن|استيشن|ستيشين)"
    r"|دريم\s*(?:ستيشن|ستشن|استيشن|ستيشين).{0,35}(?:حصري|حصرية|حصريات|خاص(?:ة)?)",
    re.IGNORECASE,
)
LIMITED_RE = re.compile(
    r"collector.?s?\s*edition|limited\s*edition|steel\s*book|steelbook|" 
    r"special\s*edition|deluxe\s*edition|premium\s*edition|ultimate\s*edition|" 
    r"نسخة\s*(?:هواة|المجمعين|محدودة|خاصة|فاخرة)|ستيل\s*بوك|اصدار\s*(?:محدود|خاص)",
    re.IGNORECASE,
)
OUT_RE = re.compile(r"out\s*of\s*stock|sold\s*out|نفد\s*(?:من\s*المخزون)?|غير\s*متوفر|غير\s*متاح|انتهت\s*الكمية", re.IGNORECASE)
IN_RE = re.compile(r"add\s*to\s*cart|pre[ -]?order\s*now|buy\s*now|أضف\s*(?:للسلة|إلى\s*السلة)|اطلب\s*الآن|احجز\s*الآن", re.IGNORECASE)

# Browser-side extraction reads Salla's documented product JSON attribute when present,
# falling back to rendered cards. No arbitrary sitewide links are treated as games.
EXTRACT_JS = r"""() => {
  const selectors = 'salla-product-card,custom-salla-product-card,.product-entry,.s-product-card-entry,[data-product-id].product-card,.product-card';
  const nodes = Array.from(document.querySelectorAll(selectors));
  const items = [];
  for (const el of nodes) {
    let prod = {};
    const raw = el.getAttribute('product');
    if (raw) { try { prod = JSON.parse(raw); } catch(e) {} }
    if (!prod || typeof prod !== 'object') prod = {};
    const a = el.querySelector('a[href*="/p"],a[href*="/product"],a[href]');
    const heading = el.querySelector('h3,h2,h4,.product-title,.product-entry__title,.s-product-card-entry__title');
    const url = prod.url || (a && a.href) || '';
    const name = prod.name || (heading && heading.innerText) || (a && a.getAttribute('title')) || '';
    const cardText = (el.innerText || el.textContent || '').trim().slice(0, 800);
    const price = prod.is_on_sale && prod.sale_price ? prod.sale_price : (prod.price || prod.starting_price || null);
    items.push({id: prod.id || el.getAttribute('data-product-id') || '', name, url, price,
      is_out_of_stock: prod.is_out_of_stock, is_available: prod.is_available,
      status: prod.status || '', text: cardText});
  }
  // On some custom themes the cards are ordinary links rather than Salla web components.
  if (!items.length) {
    const links = Array.from(document.querySelectorAll('a[href]'));
    for (const a of links) {
      const href = a.href || '';
      if (!/\/p\d{4,}(?:[/?#]|$)/i.test(href)) continue;
      const el = a.closest('article,.product-entry,li,[class*="product-card"],[class*="product-entry"]') || a;
      items.push({id: el.getAttribute('data-product-id') || '', name: (a.innerText || a.getAttribute('title') || a.querySelector('img')?.alt || '').trim(),
        url: href, price: null, status: '', text: (el.innerText || '').slice(0,800)});
    }
  }
  return items;
}"""


def canonical_url(value):
    if not value:
        return None
    url = urljoin(BASE, value)
    p = urlparse(url)
    if p.scheme != "https" or p.netloc.lower() not in ("dreamstation.com.sa", "www.dreamstation.com.sa"):
        return None
    path = re.sub(r"/+", "/", p.path).rstrip("/")
    if not path or path == "/pre-orders/c630489014":
        return None
    return urlunparse(("https", "dreamstation.com.sa", path, "", "", ""))


def stock(raw):
    status = str(raw.get("status") or "").lower().strip()
    if raw.get("is_out_of_stock") is True or status in ("out", "out-and-notify", "out_of_stock", "sold_out"):
        return "out"
    if raw.get("is_available") is True or raw.get("is_out_of_stock") is False or status in ("sale", "available"):
        return "available"
    text = str(raw.get("text") or "")
    if OUT_RE.search(text):
        return "out"
    if IN_RE.search(text):
        return "available"
    return "unknown"  # Don't invent stock status.


def normalize(raw):
    url = canonical_url(raw.get("url"))
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()
    if not name or not url or len(name) < 3:
        return None
    # Site navigation and generic categories are not product listings.
    if re.search(r"/(?:c\d+|category|collections?)(?:/|$)", url, re.I):
        return None
    product_id = str(raw.get("id") or "").strip() or url
    price = raw.get("price")
    if isinstance(price, (float, int)) and not isinstance(price, bool):
        price = f"{price:g} SAR"
    elif isinstance(price, str) and price.strip():
        price = price.strip()
        if not re.search(r"ر\.?س|sar|ريال", price, re.I):
            price += " SAR"
    else:
        match = re.search(r"(\d{1,6}(?:[.,]\d{1,2})?)\s*(?:ر\.?\s*س|SAR|ريال)", str(raw.get("text") or ""), re.I)
        price = match.group(0) if match else "Price not shown"
    words = name + " " + str(raw.get("text") or "")
    priority = "exclusive" if EXCLUSIVE_RE.search(words) else ("limited" if LIMITED_RE.search(words) else "standard")
    return {"id": product_id, "name": name, "url": url, "price": price, "stock": stock(raw), "priority": priority}


def collect(page):
    found = {}
    for page_num in range(1, 5):
        url = CATEGORY if page_num == 1 else f"{CATEGORY}?page={page_num}"
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 400:
            raise RuntimeError(f"Dream Station returned HTTP {response.status} for {url}")
        page.wait_for_timeout(2500)
        # Salla's products list may load as you scroll. Allow a few rounds.
        for _ in range(7):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(600)
        page.wait_for_timeout(700)
        page_rows = page.evaluate(EXTRACT_JS)
        new_this_page = 0
        for row in page_rows:
            item = normalize(row)
            if item and item["id"] not in found:
                found[item["id"]] = item
                new_this_page += 1
        print(f"Page {page_num}: {len(page_rows)} raw cards, {new_this_page} new unique products")
        if page_num > 1 and not new_this_page:
            break
    if not found:
        raise RuntimeError("No product cards found. Page may be blocked/changed; keeping old baseline and sending no alerts.")
    return found


def fetch_detail(page, item):
    """Improve priority classification based on product detail, only for *new* items."""
    try:
        response = page.goto(item["url"], wait_until="domcontentloaded", timeout=30000)
        if response and response.status >= 400:
            return item
        page.wait_for_timeout(1000)
        text = page.evaluate("""() => [document.querySelector('meta[name="description"]')?.content,
          document.querySelector('meta[property="og:description"]')?.content,
          ...Array.from(document.querySelectorAll('.product-description,.product-details,.s-product-page-description,[itemprop="description"]')).map(e=>e.innerText)]
          .filter(Boolean).join(' ').slice(0,4000)""")
        if EXCLUSIVE_RE.search(text or ""):
            item["priority"] = "exclusive"
        elif LIMITED_RE.search(text or "") and item["priority"] == "standard":
            item["priority"] = "limited"
    except Exception as e:
        print(f"Detail lookup unavailable for {item['url']}: {type(e).__name__}")
    return item


def notify(item, event, token, chat_id):
    ranks = {"exclusive": "🔴 URGENT — DREAM STATION EXCLUSIVE", "limited": "🟠 HIGH — COLLECTOR / LIMITED", "standard": "🟡 NEW PREORDER"}
    headline = ranks[item["priority"]]
    if event == "restock":
        headline = "🔄 RESTOCK — " + headline
    platform_match = re.search(r"\b(?:PS5|PS4|PS3|Xbox\s*(?:Series\s*[XS]|One|360)|Switch\s*2|Nintendo\s*Switch|PC)\b", item["name"], re.I)
    platform = platform_match.group(0) if platform_match else "Not specified"
    status = {"available": "Available to order", "out": "Sold out", "unknown": "Not verified"}[item["stock"]]
    msg = (f"<b>{html.escape(headline)}</b>\n\n"
           f"🎮 <b>{html.escape(item['name'])}</b>\n"
           f"🕹 Platform: {html.escape(platform)}\n"
           f"💰 Price: {html.escape(str(item['price']))}\n"
           f"📦 Availability: {status}\n\n"
           f"🔗 <a href=\"{html.escape(item['url'], quote=True)}\">OPEN DREAM STATION PREORDER</a>")
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
        "chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": False
    }, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {payload.get('description','unknown error')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-message", action="store_true", help="Send a Telegram setup confirmation only")
    args = parser.parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub repository Actions secrets.")
    if args.test_message:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            "chat_id": chat_id, "text": "✅ Dream Station watch connected! The monitor will check for new preorders and exclusives."
        }, timeout=20)
        resp.raise_for_status()
        if not resp.json().get("ok"):
            raise RuntimeError("Telegram test message rejected")
        print("Test notification sent")
        return

    prior = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"version": 1, "items": {}}
    if not isinstance(prior.get("items"), dict):
        raise RuntimeError("state.json has invalid structure")
    previous = prior["items"]
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        except Exception:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(locale="ar-SA", viewport={"width": 1366, "height": 1024}, user_agent=None)
        page = context.new_page()
        products = collect(page)
        detail_page = context.new_page()
        changes = []
        for product_id, item in products.items():
            old = previous.get(product_id)
            # Keep stronger exclusivity classifications discovered in product details.
            if old and {"exclusive": 2, "limited": 1, "standard": 0}.get(old.get("priority"), 0) > {"exclusive": 2, "limited": 1, "standard": 0}.get(item["priority"], 0):
                item["priority"] = old["priority"]
            if old is None:
                # A product never observed before. A first-run baseline is silent.
                if previous:
                    fetch_detail(detail_page, item)
                    changes.append((item, "new"))
            elif old.get("stock") == "out" and item["stock"] == "available":
                changes.append((item, "restock"))
        context.close()
        browser.close()

    # Prioritize exclusives, then other limited editions, then regular preorders.
    prio = {"exclusive": 0, "limited": 1, "standard": 2}
    changes.sort(key=lambda x: (prio[x[0]["priority"]], x[0]["name"].casefold()))
    if not previous:
        print(f"Baseline initialized with {len(products)} products; no historical products are falsely reported as new.")
    else:
        print(f"Found {len(changes)} verified changes among {len(products)} products")
    # If Telegram is down, fail *without saving new state*: next run will retry.
    for item, event in changes:
        notify(item, event, token, chat_id)
        print(f"Sent {event}: {item['name']} ({item['priority']})")
        time.sleep(0.3)

    updated = dict(previous)
    for product_id, item in products.items():
        updated[product_id] = item
    # Don't rewrite the state on a no-change run, to avoid constant Git commits.
    if updated != previous:
        STATE_PATH.write_text(json.dumps({"version": 1, "items": updated}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("State updated")
    else:
        print("No state changes")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

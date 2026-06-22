#!/usr/bin/env python3
"""
Jordan Instagram Seller Lead Generator
========================================
Pulls thousands of leads from:
  1. Meta Ad Library API  — active advertisers in Jordan (highest priority, FREE)
  2. Apify Instagram scraper results (paste exported JSON/CSV from Apify)
  3. Manual seed list enrichment

HOW TO GET THOUSANDS OF LEADS - 3 METHODS:
============================================

METHOD 1 — Meta Ad Library API (FREE, finds active ad buyers)
  1. Go to https://developers.facebook.com/tools/explorer/
  2. Click "Generate Access Token" (no special permissions needed)
  3. Run: python3 jordan_lead_scraper.py --token YOUR_TOKEN
  → Returns every business actively spending money on Instagram ads in Jordan.
    These are your BEST leads — they already have budget.

METHOD 2 — Apify Instagram Hashtag Scraper (paid, ~$5-10/month)
  1. Create account at https://apify.com
  2. Use actor: "apify/instagram-hashtag-scraper"
  3. Set hashtags (see INSTAGRAM_HASHTAGS list below)
  4. Export as CSV/JSON → run: python3 jordan_lead_scraper.py --apify apify_export.json
  → Returns thousands of posts + account handles per hashtag

METHOD 3 — PhantomBuster (paid, ~$56/month)
  Instagram Profile Scraper + Hashtag Search
  https://phantombuster.com/automations/instagram

Usage:
  python3 jordan_lead_scraper.py --token YOUR_META_TOKEN
  python3 jordan_lead_scraper.py --token YOUR_META_TOKEN --limit 10000
  python3 jordan_lead_scraper.py --apify apify_export.json
  python3 jordan_lead_scraper.py --token YOUR_TOKEN --apify apify_export.json
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Search terms for Meta Ad Library ──────────────────────────────────────────
META_SEARCH_TERMS = [
    # Arabic
    "متجر", "بيع", "ملابس", "عطور", "مجوهرات", "اكسسوارات",
    "مستحضرات", "تجميل", "عناية بالبشرة", "حلويات", "طعام",
    "أثاث", "ديكور", "هدايا", "أطفال", "رياضة", "أحذية",
    "حقائب", "عباية", "ابايا", "مكياج", "كريم", "عطر",
    "ساعات", "نظارات", "إلكترونيات", "جوال", "لاب توب",
    "مفروشات", "بسطة", "بضاعة", "توصيل", "كاش اون ديليفري",
    # English
    "shop jordan", "store amman", "boutique amman", "fashion jordan",
    "beauty jordan", "skincare jordan", "jewelry amman", "accessories jordan",
    "clothing jordan", "perfume jordan", "handmade jordan", "delivery jordan",
    "order amman", "online store jordan", "free delivery jordan",
    "cash on delivery jordan", "made in jordan",
]

# ── Hashtags to use with Apify or PhantomBuster ───────────────────────────────
INSTAGRAM_HASHTAGS = [
    # English
    "ammanshop", "jordanshop", "ammanstore", "jordanstore",
    "shopjordan", "shopamman", "boutiquejordan", "fashionjordan",
    "fashionamman", "beautyjo", "skincareJordan", "jewelryjordan",
    "accessoriesjordan", "handmadejordan", "jordanfashion",
    "ammanboutique", "jordanboutique", "ammanshopping",
    "jordanshopping", "jordanbeauty", "jordanmakeup",
    "jordanperfume", "jordanfood", "jordandessert",
    "ammandessert", "ammanfood", "jordansweets",
    "jordanjewelry", "jordangold", "ammangold",
    # Arabic (for Apify — supports Arabic hashtags)
    "الاردن", "عمان_الاردن", "متجر_الاردن", "ملابس_الاردن",
    "عطور_الاردن", "مجوهرات_الاردن", "الاردن_يبيع",
    "بيع_الاردن", "عمان_شوب", "عمان_متجر", "اكسسوارات_الاردن",
    "مكياج_الاردن", "عناية_الاردن", "حلويات_الاردن",
    "طعام_الاردن", "ديكور_الاردن", "هدايا_الاردن",
    "اردن_موضه", "بوتيك_الاردن", "ازياء_الاردن",
]

META_API_BASE = "https://graph.facebook.com/v19.0/ads_archive"

CATEGORY_MAP = {
    "ملابس": "Fashion", "عباية": "Fashion", "ابايا": "Fashion",
    "أحذية": "Fashion", "حقائب": "Fashion", "fashion": "Fashion",
    "clothing": "Fashion", "boutique": "Fashion", "ازياء": "Fashion",
    "موضه": "Fashion", "بوتيك": "Fashion",
    "عطور": "Perfumes", "perfume": "Perfumes", "عطر": "Perfumes",
    "مجوهرات": "Jewelry", "jewelry": "Jewelry", "accessories": "Jewelry",
    "اكسسوارات": "Jewelry", "ذهب": "Jewelry", "gold": "Jewelry",
    "ساعات": "Watches", "نظارات": "Eyewear",
    "تجميل": "Beauty", "عناية": "Beauty", "مستحضرات": "Beauty",
    "beauty": "Beauty", "skincare": "Beauty", "مكياج": "Beauty",
    "makeup": "Beauty", "كريم": "Beauty",
    "طعام": "Food", "حلويات": "Food", "food": "Food",
    "dessert": "Food", "sweets": "Food", "مطبخ": "Food",
    "ديكور": "Home & Decor", "أثاث": "Home & Decor", "مفروشات": "Home & Decor",
    "handmade": "Handmade", "هدايا": "Handmade", "يدوي": "Handmade",
    "رياضة": "Sports", "أطفال": "Kids",
    "إلكترونيات": "Electronics", "جوال": "Electronics", "لاب توب": "Electronics",
}


def guess_category(text: str) -> str:
    text_lower = text.lower()
    for kw, cat in CATEGORY_MAP.items():
        if kw in text_lower:
            return cat
    return "General"


def fetch_meta_ads(token: str, limit: int) -> list[dict]:
    """Query Meta Ad Library API for active Jordan advertisers."""
    print(f"\n[Meta Ad Library] Searching {len(META_SEARCH_TERMS)} keyword terms...")
    leads: list[dict] = []
    seen_page_ids: set[str] = set()

    for i, term in enumerate(META_SEARCH_TERMS, 1):
        print(f"  [{i}/{len(META_SEARCH_TERMS)}] Searching: '{term}'")
        params = {
            "search_terms": term,
            "ad_reached_countries": '["JO"]',
            "ad_active_status": "ACTIVE",
            "ad_type": "ALL",
            "fields": "page_id,page_name,ad_creative_bodies,ad_creative_link_descriptions,ad_delivery_start_time,publisher_platforms",
            "limit": 100,
            "access_token": token,
        }

        url: str | None = META_API_BASE
        while url:
            try:
                resp = requests.get(
                    url,
                    params=params if url == META_API_BASE else {},
                    timeout=15,
                )
                if resp.status_code in (400, 401, 403):
                    err = resp.json().get("error", {})
                    print(f"     ✗ API error: {err.get('message', resp.text[:100])}")
                    url = None
                    break
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                print(f"     ✗ Network error: {e}")
                break

            for ad in data.get("data", []):
                page_id = ad.get("page_id", "")
                if page_id in seen_page_ids:
                    continue
                seen_page_ids.add(page_id)

                page_name = ad.get("page_name", "")
                bodies = ad.get("ad_creative_bodies") or []
                body_text = " ".join(bodies)
                platforms = ", ".join(ad.get("publisher_platforms") or [])

                leads.append({
                    "Lead #": len(leads) + 1,
                    "Business Name": page_name,
                    "Instagram Handle": "",
                    "Instagram URL": f"https://www.instagram.com/{re.sub(r'[^a-zA-Z0-9._]', '', page_name).lower()}/",
                    "Facebook Page ID": page_id,
                    "Facebook Page URL": f"https://www.facebook.com/{page_id}",
                    "Followers (approx)": "",
                    "Category": guess_category(f"{page_name} {body_text} {term}"),
                    "Location": "Jordan",
                    "Source": "Meta Ad Library (ACTIVE AD)",
                    "Ad Status": "ACTIVE",
                    "Ad Platforms": platforms,
                    "Ad Running Since": ad.get("ad_delivery_start_time", ""),
                    "Order Method": "Paid Ads",
                    "Ad Potential": "High",
                    "Priority Level": "🔥 Hot — Active Advertiser",
                    "Search Term Used": term,
                    "Ad Body Preview": body_text[:250],
                    "Outreach Angle": "Already spending on ads — pitch proper online store to convert ad traffic better",
                    "Notes": f"Active Meta/Instagram advertiser in Jordan. Keyword: '{term}'",
                })

            print(f"     ✓ {len(seen_page_ids)} unique advertisers total")

            if len(leads) >= limit:
                print(f"  Reached limit of {limit}. Stopping.")
                return leads

            url = data.get("paging", {}).get("next")
            params = {}
            time.sleep(0.25)

    return leads


def process_apify_export(filepath: str, existing_count: int) -> list[dict]:
    """Process an Apify Instagram scraper JSON or CSV export."""
    print(f"\n[Apify Import] Loading: {filepath}")
    path = Path(filepath)
    leads: list[dict] = []
    seen: set[str] = set()

    try:
        if path.suffix.lower() == ".json":
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
        elif path.suffix.lower() == ".csv":
            with open(path, encoding="utf-8-sig") as f:
                items = list(csv.DictReader(f))
        else:
            print(f"  ✗ Unsupported file type: {path.suffix}")
            return []
    except Exception as e:
        print(f"  ✗ Failed to load file: {e}")
        return []

    for item in items:
        # Apify Instagram Hashtag Scraper fields
        username = (
            item.get("ownerUsername") or
            item.get("username") or
            item.get("owner", {}).get("username", "") if isinstance(item.get("owner"), dict) else ""
        )
        if not username or username in seen:
            continue
        seen.add(username)

        full_name = (
            item.get("ownerFullName") or
            item.get("full_name") or
            item.get("displayName") or
            username
        )
        followers = (
            item.get("followersCount") or
            item.get("followers") or
            item.get("edge_followed_by", {}).get("count", "") if isinstance(item.get("edge_followed_by"), dict) else ""
        )
        hashtags_used = item.get("hashtags") or item.get("caption", "")
        caption = item.get("caption") or item.get("alt") or ""

        fc = int(str(followers).replace(",", "")) if str(followers).isdigit() else 0
        priority = "🔥 Hot" if fc > 50000 else ("⚡ Warm" if fc > 5000 else "🌱 Cold")
        ad_potential = "High" if fc > 50000 else ("Medium" if fc > 5000 else "Low")

        leads.append({
            "Lead #": existing_count + len(leads) + 1,
            "Business Name": full_name,
            "Instagram Handle": f"@{username}",
            "Instagram URL": f"https://www.instagram.com/{username}/",
            "Facebook Page ID": "",
            "Facebook Page URL": "",
            "Followers (approx)": followers,
            "Category": guess_category(f"{full_name} {caption}"),
            "Location": "Jordan",
            "Source": "Apify Instagram Scraper",
            "Ad Status": "Unknown — check Meta Ad Library",
            "Ad Platforms": "Instagram",
            "Ad Running Since": "",
            "Order Method": "DM / Unknown",
            "Ad Potential": ad_potential,
            "Priority Level": priority,
            "Search Term Used": str(hashtags_used)[:100],
            "Ad Body Preview": caption[:250],
            "Outreach Angle": "Seller found via hashtag — pitch online store to replace DM ordering",
            "Notes": "Verify profile is active before outreach.",
        })

    print(f"  ✓ Loaded {len(leads)} unique accounts from Apify export")
    return leads


def save_csv(leads: list[dict], path: str) -> str:
    if not leads:
        print("\nNo leads to save.")
        return ""

    for i, lead in enumerate(leads, 1):
        lead["Lead #"] = i

    fieldnames = [
        "Lead #", "Business Name", "Instagram Handle", "Instagram URL",
        "Facebook Page ID", "Facebook Page URL", "Followers (approx)",
        "Category", "Location", "Source", "Ad Status", "Ad Platforms",
        "Ad Running Since", "Order Method", "Ad Potential", "Priority Level",
        "Search Term Used", "Ad Body Preview", "Outreach Angle", "Notes",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(leads)

    return path


def print_summary(leads: list[dict], path: str) -> None:
    total = len(leads)
    hot   = sum(1 for l in leads if "Hot"  in l.get("Priority Level", ""))
    warm  = sum(1 for l in leads if "Warm" in l.get("Priority Level", ""))
    cold  = sum(1 for l in leads if "Cold" in l.get("Priority Level", ""))
    ads   = sum(1 for l in leads if l.get("Ad Status") == "ACTIVE")

    cats: dict[str, int] = {}
    for l in leads:
        c = l.get("Category", "General")
        cats[c] = cats.get(c, 0) + 1

    print("\n" + "=" * 60)
    print("  JORDAN LEAD GENERATION — COMPLETE")
    print("=" * 60)
    print(f"  Total leads         : {total:,}")
    print(f"  Active ad runners   : {ads:,}  ← highest value")
    print(f"  🔥 Hot              : {hot:,}")
    print(f"  ⚡ Warm             : {warm:,}")
    print(f"  🌱 Cold             : {cold:,}")
    print(f"\n  Category breakdown:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1])[:10]:
        bar = "█" * min(30, count // max(1, total // 30))
        print(f"    {cat:<20} {count:>5,}  {bar}")
    print(f"\n  Output file: {path}")
    print("=" * 60)
    print("""
NEXT STEPS FOR YOUR SALES TEAM:
  1. Filter column 'Ad Status' = ACTIVE  → call/DM these first
  2. Filter column 'Priority Level' = Hot → large audiences, easy pitch
  3. Verify Instagram handles before bulk outreach
  4. Check Meta Ad Library manually for any account:
     https://www.facebook.com/ads/library/?country=JO
""")


def main():
    parser = argparse.ArgumentParser(
        description="Jordan Instagram Lead Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--token", help="Meta Graph API access token")
    parser.add_argument("--apify", help="Path to Apify export file (JSON or CSV)")
    parser.add_argument("--limit", type=int, default=10000, help="Max leads from Meta API (default: 10000)")
    parser.add_argument("--output", default="jordan_leads_FULL.csv", help="Output CSV filename")
    args = parser.parse_args()

    if not args.token and not args.apify:
        print(__doc__)
        print("\nERROR: Provide --token (Meta API) and/or --apify (Apify export file).\n")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output.replace(".csv", f"_{timestamp}.csv")
    all_leads: list[dict] = []

    if args.token:
        meta_leads = fetch_meta_ads(args.token, args.limit)
        all_leads.extend(meta_leads)

    if args.apify:
        apify_leads = process_apify_export(args.apify, len(all_leads))
        all_leads.extend(apify_leads)

    saved = save_csv(all_leads, output_path)
    if saved:
        print_summary(all_leads, saved)


if __name__ == "__main__":
    main()

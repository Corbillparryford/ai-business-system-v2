"""
content/product_scout.py
========================
Product discovery from TikTok trending and Google Trends RSS.
Returns unified list for Claude to score and script.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from core.config import TIKTOK_ACCESS_TOKEN

log = logging.getLogger(__name__)


def _tiktok_trending() -> list:
    if not TIKTOK_ACCESS_TOKEN:
        return []
    today = datetime.utcnow().strftime("%Y%m%d")
    try:
        r = requests.post(
            "https://open.tiktokapis.com/v2/research/video/query/",
            headers={
                "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
                "Content-Type":  "application/json",
            },
            json={
                "query": {"and": [{"operation": "IN", "field_name": "hashtag_name",
                    "field_values": ["tiktokmademebuyit","amazonfinds","tiktokshop"]}]},
                "start_date": today, "end_date": today,
                "max_count": 20,
                "fields": "id,desc,view_count,like_count",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        out = []
        for v in r.json().get("data", {}).get("videos", []):
            desc = v.get("desc", "")
            if any(k in desc.lower() for k in ["link","shop","buy","amazon"]):
                out.append({
                    "source":         "TIKTOK",
                    "description":    desc[:200],
                    "view_count":     v.get("view_count", 0),
                    "asin":           None,
                    "affiliate_url":  None,
                    "tiktok_shop_url": None,
                })
        return out
    except Exception as e:
        log.warning("TikTok fetch: %s", e)
        return []


def _google_trends() -> list:
    try:
        r = requests.get(
            "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        if r.status_code != 200:
            return []
        root  = ET.fromstring(r.content)
        items = root.findall(".//item")
        return [{
            "source":         "GOOGLE_TRENDS",
            "description":    item.findtext("title", ""),
            "view_count":     0,
            "asin":           None,
            "affiliate_url":  None,
            "tiktok_shop_url": None,
        } for item in items[:10] if item.findtext("title")]
    except Exception as e:
        log.warning("Google Trends fetch: %s", e)
        return []


def build_product_candidates() -> list:
    candidates = _tiktok_trending() + _google_trends()
    seen, clean = set(), []
    for c in candidates:
        key = c["description"][:50].lower()
        if key not in seen:
            seen.add(key)
            clean.append(c)
    clean.sort(key=lambda x: x.get("view_count", 0), reverse=True)
    return clean[:10]

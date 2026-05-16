"""
content/content_engine.py
=========================
Content engine — runs every 3 hours.
Low priority: never crashes the core sports/trading system.
"""

import json
import logging
import time

from core.claude_client import call_content_brain
from core.db import queue_content_item, get_pending_content, mark_content_posted, already_queued_today
from discord.poster import post_signal, post_health_alert
from content.product_scout import build_product_candidates
from content.video_builder import generate_voiceover, fetch_pexels_clips, assemble_video
from content.publisher import publish_tiktok, publish_instagram

log = logging.getLogger(__name__)

TIKTOK_COOLDOWN = 300   # 5 min between TikTok posts


def run_content_engine():
    log.info("Content engine cycle starting")
    try:
        candidates = build_product_candidates()
        result     = call_content_brain(candidates)
        batch      = result.get("content_batch", [])

        if not batch:
            log.warning("Content brain returned empty batch")
            return

        batch.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        for pkg in batch:
            if not already_queued_today(pkg.get("product_name", "")):
                queue_content_item(pkg)

        for item in get_pending_content(limit=3):
            name   = item["product_name"] or "product"
            script = json.loads(item.get("script_json") or "{}")
            pkg    = {
                "product_name":    name,
                "hook":            item.get("hook", ""),
                "caption":         script.get("15_20", ""),
                "script":          script,
                "affiliate_url":   item.get("affiliate_url"),
                "tiktok_shop_url": item.get("tiktok_shop_url"),
                "platform":        item.get("platform", "AMAZON"),
                "priority_score":  item.get("priority_score", 5.0),
            }
            vo         = generate_voiceover(script, name)
            clips      = fetch_pexels_clips(name, count=4)
            video_path = assemble_video(pkg, vo, clips)
            post_url   = None
            if video_path and video_path.exists():
                post_url = publish_tiktok(video_path, pkg) or publish_instagram(video_path, pkg)
            mark_content_posted(item["id"], post_url or "build_failed")
            post_signal({**pkg, "post_url": post_url or "pending"}, "content")
            log.info("Content item complete: %s → %s", name, post_url or "no URL")
            time.sleep(TIKTOK_COOLDOWN)

    except Exception as e:
        log.error("Content engine error: %s", e)
        try:
            post_health_alert("content_engine", str(e))
        except Exception:
            pass

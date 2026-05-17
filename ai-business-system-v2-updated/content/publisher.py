"""
content/publisher.py — TikTok and Instagram publishing.
"""

import logging
from pathlib import Path

import requests

from core.config import TIKTOK_ACCESS_TOKEN, INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID

log = logging.getLogger(__name__)


def publish_tiktok(video_path: Path, package: dict) -> str | None:
    if not TIKTOK_ACCESS_TOKEN or not video_path or not video_path.exists():
        return None
    size = video_path.stat().st_size
    try:
        init = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={"Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
                     "Content-Type": "application/json"},
            json={
                "post_info": {
                    "title": package.get("hook", "")[:150],
                    "description": package.get("caption", "")[:2200],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False, "disable_comment": False,
                    "disable_stitch": False, "video_cover_timestamp_ms": 1000,
                },
                "source_info": {
                    "source": "FILE_UPLOAD", "video_size": size,
                    "chunk_size": size, "total_chunk_count": 1,
                },
            },
            timeout=15,
        )
        if init.status_code != 200:
            log.error("TikTok init HTTP %d", init.status_code)
            return None
        data = init.json().get("data", {})
        url  = data.get("upload_url")
        pid  = data.get("publish_id")
        if not url:
            return None
        with open(video_path, "rb") as f:
            up = requests.put(
                url,
                headers={"Content-Range": f"bytes 0-{size-1}/{size}",
                         "Content-Length": str(size), "Content-Type": "video/mp4"},
                data=f, timeout=180,
            )
        if up.status_code in (200, 201):
            log.info("TikTok published: %s", pid)
            return f"https://www.tiktok.com/@your_account/video/{pid}"
        log.error("TikTok upload HTTP %d", up.status_code)
        return None
    except Exception as e:
        log.error("TikTok publish error: %s", e)
        return None


def publish_instagram(video_path: Path, package: dict) -> str | None:
    if not INSTAGRAM_ACCESS_TOKEN or not INSTAGRAM_USER_ID:
        return None
    log.info("Instagram publish: requires public CDN URL — pending S3 integration")
    return None

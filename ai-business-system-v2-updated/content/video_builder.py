"""
content/video_builder.py
========================
ElevenLabs TTS + Pexels clips + MoviePy assembly → 1080x1920 .mp4
"""

import logging
import os
from pathlib import Path

import requests

from core.config import ELEVENLABS_API_KEY, PEXELS_KEY

log     = logging.getLogger(__name__)
OUT_DIR = Path(os.path.join(os.getcwd(), "content_output"))
os.makedirs(OUT_DIR, exist_ok=True)

VOICE_ID = "21m00Tcm4TlvDq8ikWAM"   # ElevenLabs "Rachel"


def generate_voiceover(script: dict, product_name: str) -> Path | None:
    if not ELEVENLABS_API_KEY:
        return None
    text = " ".join(str(v) for v in script.values())
    safe = "".join(c if c.isalnum() else "_" for c in product_name)[:40]
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_monolingual_v1",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        path = OUT_DIR / f"vo_{safe}.mp3"
        path.write_bytes(r.content)
        return path
    except Exception as e:
        log.error("Voiceover error: %s", e)
        return None


def fetch_pexels_clips(query: str, count: int = 4) -> list:
    if not PEXELS_KEY:
        return []
    safe  = "".join(c if c.isalnum() else "_" for c in query)[:30]
    paths = []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "orientation": "portrait", "per_page": count},
            timeout=10,
        )
        for i, v in enumerate(r.json().get("videos", [])[:count]):
            files = sorted(v.get("video_files", []), key=lambda f: f.get("width", 9999))
            if not files:
                continue
            path = OUT_DIR / f"clip_{safe}_{i}.mp4"
            with open(path, "wb") as fh:
                for chunk in requests.get(files[0]["link"], timeout=60, stream=True).iter_content(1 << 20):
                    fh.write(chunk)
            paths.append(path)
    except Exception as e:
        log.error("Pexels error: %s", e)
    return paths


def assemble_video(package: dict, vo_path, clip_paths: list) -> Path | None:
    if not clip_paths:
        return None
    try:
        from moviepy.editor import (
            VideoFileClip, AudioFileClip, TextClip,
            CompositeVideoClip, concatenate_videoclips,
        )
        W, H   = 1080, 1920
        clips  = []
        for p in clip_paths:
            try:
                c = VideoFileClip(str(p)).without_audio().resize(height=H)
                if c.w > W:
                    x1 = (c.w - W) // 2
                    c  = c.crop(x1=x1, x2=x1 + W)
                clips.append(c.subclip(0, min(5.0, c.duration)))
            except Exception:
                pass
        if not clips:
            return None
        base = concatenate_videoclips(clips, method="compose").subclip(0, 20)
        if vo_path and Path(str(vo_path)).exists():
            try:
                base = base.set_audio(AudioFileClip(str(vo_path)).subclip(0, 20))
            except Exception:
                pass
        segs     = list((package.get("script") or {}).values())[:4]
        overlays = []
        for i, text in enumerate(segs):
            try:
                txt = TextClip(str(text)[:80], fontsize=44, color="white",
                               font="DejaVu-Sans-Bold", stroke_color="black",
                               stroke_width=2, method="caption", size=(W - 80, None))
                overlays.append(
                    txt.set_position(("center", 1550)).set_start(i * 5).set_duration(5)
                )
            except Exception:
                pass
        final  = CompositeVideoClip([base] + overlays)
        safe   = "".join(c if c.isalnum() else "_" for c in package.get("product_name","video"))[:40]
        output = OUT_DIR / f"{safe}.mp4"
        final.write_videofile(str(output), fps=30, codec="libx264",
                              audio_codec="aac", logger=None)
        return output
    except Exception as e:
        log.error("Video assembly error: %s", e)
        return None

import asyncio
import os
import random
import re
import json
import glob
import logging

import aiohttp
import yt_dlp
import requests
from youtubesearchpython.__future__ import VideosSearch
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message

from AviaxMusic.utils.database import is_on_off
from AviaxMusic.utils.formatters import time_to_seconds
import config
from config import API_URL, API_KEY

log = logging.getLogger(__name__)

def cookie_txt_file() -> str:
    """
    Pick a random .txt cookie file from cookies/ directory.
    """
    cookie_dir = os.path.join(os.getcwd(), "cookies")
    files = [f for f in os.listdir(cookie_dir) if f.endswith(".txt")]
    return os.path.join(cookie_dir, random.choice(files))


class YouTubeAPI:
    def __init__(self):
        self.base     = "https://www.youtube.com/watch?v="
        self.regex    = r"(?:youtube\.com|youtu\.be)"
        self.listbase = "https://youtube.com/playlist?list="
        self.reg      = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def get_from_api(self, link: str) -> tuple[int, str]:
        """
        Fallback to external API if yt-dlp fails.
        Expects JSON { "url": "...", "code": 1 }
        """
        try:
            async with aiohttp.ClientSession() as session:
                params = {"key": API_KEY, "url": link}
                async with session.get(API_URL, params=params, timeout=15) as r:
                    data = await r.json()
            return data.get("code", 0), data.get("url", "")
        except Exception as e:
            log.error(f"API fallback error: {e}")
            return 0, ""

    async def exists(self, link: str, videoid: bool = False) -> bool:
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message: Message) -> str | None:
        # your existing logic...
        pass

    async def details(self, link: str, videoid: bool = False):
        # your existing logic...
        pass

    # ── Direct stream URL (primary: yt-dlp, fallback: API) ─────────────────
    async def video(self, link: str, videoid: bool = False) -> tuple[int, str]:
        if videoid:
            link = self.base + link
        link = link.split("&")[0]

        # 1) Try yt-dlp CLI with cookies
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "--cookies", cookie_txt_file(),
                "-g", "-f", "bestaudio/best",
                link,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            if out:
                url = out.decode().splitlines()[0].strip()
                return 1, url
            else:
                raise RuntimeError(err.decode().strip() or "yt-dlp returned no output")
        except Exception as e:
            log.warning(f"yt-dlp stream failed: {e}. Falling back to API.")
            return await self.get_from_api(link)

    # ── List available formats ────────────────────────────────────────────────
    async def formats(self, link: str, videoid: bool = False):
        if videoid:
            link = self.base + link
        link = link.split("&")[0]
        ydl_opts = {
            "quiet":      True,
            "cookiefile": cookie_txt_file(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=False)
        formats = []
        for fmt in info.get("formats", []):
            if "dash" in fmt.get("format", "").lower():
                continue
            if all(k in fmt for k in ("format","filesize","format_id","ext","format_note")):
                formats.append({
                    "format":      fmt["format"],
                    "filesize":    fmt["filesize"],
                    "format_id":   fmt["format_id"],
                    "ext":         fmt["ext"],
                    "format_note": fmt["format_note"],
                    "yturl":       link,
                })
        return formats, link

    # ── Download or stream (primary: yt-dlp, fallback: API) ──────────────────
    async def download(
        self,
        link: str,
        mystic,
        video: bool = False,
        videoid: bool = False,
        songaudio: bool = False,
        songvideo: bool = False,
        format_id: str | None = None,
        title: str | None = None,
    ) -> str | tuple[str, bool]:
        if videoid:
            link = self.base + link

        async def audio_dl():
            opts = {
                "format":       "bestaudio/best",
                "outtmpl":      "downloads/%(id)s.%(ext)s",
                "geo_bypass":   True,
                "nocheckcertificate": True,
                "quiet":        True,
                "cookiefile":   cookie_txt_file(),
                "no_warnings":  True,
            }
            ydl = yt_dlp.YoutubeDL(opts)
            info = ydl.extract_info(link, download=False)
            path = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if not os.path.exists(path):
                ydl.download([link])
            return path

        async def video_dl():
            opts = {
                "format":       "(bestvideo[height<=?720][ext=mp4])+"\
                                "(bestaudio[ext=m4a])",
                "outtmpl":      "downloads/%(id)s.%(ext)s",
                "geo_bypass":   True,
                "nocheckcertificate": True,
                "quiet":        True,
                "cookiefile":   cookie_txt_file(),
                "no_warnings":  True,
            }
            ydl = yt_dlp.YoutubeDL(opts)
            info = ydl.extract_info(link, download=False)
            path = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if not os.path.exists(path):
                ydl.download([link])
            return path

        # 2) If streaming only, reuse video() for a URL
        if video:
            code, url = await self.video(link, videoid)
            return url, False

        # 3) Otherwise download via executor
        loop = asyncio.get_running_loop()
        try:
            file_path = await loop.run_in_executor(None, audio_dl)
            return file_path, True
        except Exception as e:
            log.warning(f"yt-dlp download failed: {e}. Falling back to API.")
            # fallback to API (assumes API returns direct URL)
            code, url = await self.get_from_api(link)
            if code:
                # mystic expects a file path? you may need to stream via URL
                return url, False
            raise RuntimeError("Both yt-dlp and API download failed")

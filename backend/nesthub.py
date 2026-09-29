"""Bounded, per-segment 720p conversion for Nest Hub's HLS decoder."""
from __future__ import annotations

import subprocess
import threading

from . import seg_cache

_workers = threading.BoundedSemaphore(2)


def transcode(url: str, data: bytes, *, web: bool = False) -> bytes:
    key = ("web-720p:" if web else "nesthub-720p:") + url
    cached = seg_cache.get(key)
    if cached is not None:
        return cached
    if not data or len(data) > 30_000_000:
        raise ValueError("Nest Hub 影片分段大小異常")
    if not _workers.acquire(timeout=10):
        raise TimeoutError("Nest Hub 影片轉換忙碌，請重試")
    try:
        cached = seg_cache.get(key)
        if cached is not None:
            return cached
        import imageio_ffmpeg
        # Keep the source PTS so seeking and episode progress use the original
        # VOD timeline. FFmpeg only reads these validated bytes, never a URL.
        scale = "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
        # Web playback preserves frame rate and caps bandwidth; receiver tuning stays unchanged.
        rate = ["-crf", "25", "-maxrate", "1800k", "-bufsize", "3600k"] if web else ["-crf", "22"]
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-protocol_whitelist", "pipe", "-f", "mpegts", "-copyts", "-i", "pipe:0",
                   "-map", "0:v:0", "-map", "0:a:0?",
                   "-vf", scale if web else scale + ",fps=30",
                   "-c:v", "libx264", "-preset", "veryfast", *rate, "-threads", "2",
                   "-profile:v", "main", "-level:v", "3.1", "-pix_fmt", "yuv420p", "-bf", "0",
                   "-c:a", "copy", "-mpegts_copyts", "1", "-muxdelay", "0", "-muxpreload", "0",
                   "-fs", "16000000", "-f", "mpegts", "pipe:1"]
        try:
            result = subprocess.run(command, input=data, capture_output=True, timeout=20,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise TimeoutError("Nest Hub 影片分段轉換逾時，請重試") from e
        if result.returncode or not result.stdout or len(result.stdout) >= 16_000_000:
            raise RuntimeError("此影片分段無法轉成 Nest Hub 相容格式，請改選其他來源")
        seg_cache.put(key, result.stdout)
        return result.stdout
    finally:
        _workers.release()

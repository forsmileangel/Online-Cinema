"""Decide when a cast stream should drop to 720p, and why.

The receiver keeps the highest rendition unless it keeps stalling. A stall
while this computer already holds the next segments means the TV or its Wi-Fi
cannot keep up ("receiver"); a stall while the read-ahead itself falls behind
means the source is too slow for this computer ("upstream").
"""
from __future__ import annotations

from collections import deque

STALL_SECONDS = 3.0  # Buffering this long counts as one stall.
LONG_STALL = 10.0  # One stall this long is enough.
STALL_WINDOW = 180.0  # Two stalls within this many seconds are enough.
STARTUP_GRACE = 20.0  # Initial buffering after a load never counts.
SEEK_GRACE = 10.0
JUMP_SECONDS = 15.0  # A larger playback jump between polls is a seek.
LOW_LEAD = 20.0  # Less read-ahead than this when stalling means the source is slow.


class Governor:
    def __init__(self, now: float):
        self.reset(now)

    def reset(self, now: float) -> None:
        self.quiet_until = now + STARTUP_GRACE
        self.buffering_since = None
        self.counted = False
        self.stalls: deque[float] = deque()
        self.last_time = None
        self.done = False

    def seeked(self, now: float) -> None:
        self.quiet_until = max(self.quiet_until, now + SEEK_GRACE)
        self.buffering_since = None
        self.counted = False

    def stop(self) -> None:
        """No further decisions for this episode."""
        self.done = True

    def observe(self, state: dict, ahead: dict | None, now: float) -> str | None:
        """'receiver', 'upstream' or None, from one receiver status poll."""
        if self.done:
            return None
        position = float(state.get("current_time") or 0)
        if self.last_time is not None and abs(position - self.last_time) > JUMP_SECONDS:
            self.seeked(now)
        self.last_time = position
        if not state.get("buffering") or state.get("paused") or now < self.quiet_until:
            self.buffering_since = None
            self.counted = False
            return None
        if self.buffering_since is None:
            self.buffering_since = now
        stalled = now - self.buffering_since
        if stalled >= STALL_SECONDS and not self.counted:
            self.counted = True
            self.stalls.append(now)
        while self.stalls and now - self.stalls[0] > STALL_WINDOW:
            self.stalls.popleft()
        if stalled < LONG_STALL and len(self.stalls) < 2:
            return None
        if not ahead:
            return None  # Nothing read ahead (MP4 / local file): no basis to judge.
        lead = float(ahead.get("lead_seconds") or 0)
        ratio = ahead.get("ratio")
        if lead >= LOW_LEAD:
            self.done = True
            return "receiver"
        if ratio is not None and ratio < 1.0:
            self.done = True
            return "upstream"
        return None  # The read-ahead is still catching up (e.g. right after a seek).

"""Rails' d3 layer: the decision models behind Rails' tools, and where each question goes.

Three local layers, each with its own Desktop on/off shortcut:
  eyes  - "Computer Use Vision": d3-turbo-INT4 on :8899. Screenshots, images, video, Box and Track questions.
  smart - "Decision Second Opinion": d3-ultra 4-bit on :8898. Text decisions; beat d1 on Willie's routing test.
  fast  - "Decision Guard": d1-3B on :8089. Text only, ~0.2 s; the fallback when the d3 layers are off.

How to ask (d3's own guide, decision.vllm-sr.ai/docs and vllm-sr INSTANCE_MODES.md):
  - every question about one input goes in ONE request: the input is read once;
  - a Box answer always comes with a box, even when the thing is absent: read its confidence, and ask a yes/no
    "is it there" in the same request before acting on it;
  - Box edges are fractions of the image as sent (top-left origin); images are read at up to 1.6 megapixels;
  - write instructions as you would ask a colleague, options in a few words; keep the state short.
The tools use /v1/decisions, which answers a failed question in place instead of failing the whole request.
A layer that is off is never silently replaced for media: the tool says which shortcut to turn on (Rule #1).
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

EYES = "http://127.0.0.1:8899"   # Computer Use Vision (d3-turbo-INT4)
SMART = "http://127.0.0.1:8898"  # Decision Second Opinion (d3-ultra 4-bit)
FAST = "http://127.0.0.1:8089"   # Decision Guard (d1-3B)
LAYER_NAMES = {EYES: "Computer Use Vision (d3-turbo)", SMART: "Decision Second Opinion (d3-ultra)",
               FAST: "Decision Guard (d1)"}
TIMEOUT_S = 180       # a screenshot with many boxes takes ~7 s, a long document much longer
HEALTH_TTL_S = 15     # how long a "this layer is up/down" answer is trusted
IMAGE_MAX_BYTES = 8_000_000
VIDEO_MAX_BYTES = 32_000_000
FOUND_AT = 0.50       # "is it there?" must be at least this sure ...
BOX_AT = 0.10         # ... and the box this confident, or screen_find says not found (box test 2026-10-27:
                      # 17 real targets scored 0.14-1.00, 3 absent ones 0.00)
SURE_BOX_AT = 0.30    # below this a found box is flagged "check after clicking"
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}
VISION_TYPES = ("box", "track")

_health: dict[str, tuple[float, bool]] = {}


class D3Error(Exception):
    """A question Rails could not get answered; the message says why in plain words."""


def _http(url: str, body: dict | None = None, timeout: float = TIMEOUT_S) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data, {"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise D3Error(f"{url} answered {error.code}: {detail}") from error


def ready(base: str) -> bool:
    """Is this layer loaded and answering? Cached for HEALTH_TTL_S."""
    now = time.monotonic()
    seen = _health.get(base)
    if seen and now - seen[0] < HEALTH_TTL_S:
        return seen[1]
    try:
        up = _http(base + "/health", timeout=3).get("status") in ("ready", "ok")
    except Exception:  # noqa: BLE001 - down is an answer
        up = False
    _health[base] = (now, up)
    return up


def ask(base: str, body: dict) -> dict:
    """One request to one layer -> its full reply. d1 only has /v1/systemone; d3 uses /v1/decisions."""
    path = "/v1/systemone" if base == FAST else "/v1/decisions"
    return _http(base + path, body)


# ── media ─────────────────────────────────────────────────────────────────────────────────────────────

def _media(path: str | os.PathLike) -> tuple[bytes, str]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise D3Error(f"no such file: {p}")
    kind = MEDIA_TYPES.get(p.suffix.lower())
    if kind is None:
        raise D3Error(f"{p.name}: d3 reads PNG, JPEG, WebP images and MP4, WebM, MOV, MKV videos")
    data = p.read_bytes()
    limit = VIDEO_MAX_BYTES if kind.startswith("video/") else IMAGE_MAX_BYTES
    if len(data) > limit:
        raise D3Error(f"{p.name} is {len(data) / 1e6:.1f} MB; d3 takes at most {limit / 1e6:.0f} MB")
    return data, kind


def data_url(path: str | os.PathLike) -> str:
    data, kind = _media(path)
    return f"data:{kind};base64," + base64.b64encode(data).decode()


def image_size(path: str | os.PathLike) -> tuple[int, int]:
    """(width, height) from the file header: PNG, JPEG or WebP. No image library needed."""
    data, _ = _media(path)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        if chunk == b"VP8 ":
            return struct.unpack("<H", data[26:28])[0] & 0x3FFF, struct.unpack("<H", data[28:30])[0] & 0x3FFF
    raise D3Error(f"{Path(path).name}: could not read the picture's size")


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # the running Hermes knows its own home
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")


def latest_screenshot() -> Path:
    """The newest copy computer_use saved of a capture ($HERMES_HOME/cache/images/computer_use_*)."""
    folder = hermes_home() / "cache" / "images"
    shots = sorted(folder.glob("computer_use_*.*"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    if not shots:
        raise D3Error("no screenshot yet: run computer_use(action='capture') first, or pass image_path")
    return shots[-1]


# ── routing ───────────────────────────────────────────────────────────────────────────────────────────

def _types(questions: dict) -> set[str]:
    return {str(q.get("type")) for q in (questions or {}).values() if isinstance(q, dict)}


def needs_eyes(body: dict) -> bool:
    if body.get("images") or body.get("videos") or body.get("assets"):
        return True
    if _types(body.get("questions")) & set(VISION_TYPES):
        return True
    return any(needs_eyes(s) for s in body.get("states") or [] if isinstance(s, dict))


def pick_layer(body: dict, use: str = "auto") -> str:
    """Which layer answers. Media and Box/Track need the eyes; text goes smart -> eyes -> fast.
    Raises D3Error naming the shortcut to turn on when nothing suitable is up."""
    if use == "fast" or (use == "auto" and not needs_eyes(body) and not ready(SMART) and not ready(EYES)):
        if needs_eyes(body) or body.get("states") or body.get("assets") or body.get("options"):
            raise D3Error("this question needs d3 (pictures, video, several inputs or long text). Turn on "
                          "'Computer Use Vision' or 'Decision Second Opinion' on the Desktop")
        if not ready(FAST):
            raise D3Error("no decision model is on. Turn on 'Decision Guard' (and for pictures 'Computer Use Vision') "
                          "on the Desktop")
        return FAST
    if needs_eyes(body) or use == "vision":
        if not ready(EYES):
            raise D3Error("pictures and video need 'Computer Use Vision'. Turn it on from the Desktop (it takes about "
                          "5 minutes to load)")
        return EYES
    if use in ("auto", "smart") and ready(SMART):
        return SMART
    if ready(EYES):
        return EYES
    raise D3Error("'Decision Second Opinion' is off. Turn it on from the Desktop, or ask with use='fast' for d1")


def decide(body: dict, use: str = "auto") -> dict:
    """Answer one request on the right layer -> {answered_by, ms, answers, states?, usage}."""
    base = pick_layer(body, use)
    if base == FAST:
        body = {"state": body.get("state"), "questions": body.get("questions")}
    started = time.perf_counter()
    reply = ask(base, body)
    out = {"answered_by": LAYER_NAMES[base], "ms": round((time.perf_counter() - started) * 1000),
           "answers": reply.get("answers", {})}
    for key in ("states", "usage"):
        if key in reply:
            out[key] = reply[key]
    return out


def box_in_pixels(answer: dict, width: int, height: int) -> dict:
    """A Box answer (fractions) -> pixel edges and the centre to click, in the image's own pixels."""
    b = answer.get("box") or {}
    left, right = round(b.get("left", 0) * width), round(b.get("right", 0) * width)
    top, bottom = round(b.get("top", 0) * height), round(b.get("bottom", 0) * height)
    return {"box_px": {"left": left, "top": top, "right": right, "bottom": bottom},
            "click_at": [round((left + right) / 2), round((top + bottom) / 2)],
            "confidence": round(float(answer.get("confidence", 0.0)), 3)}

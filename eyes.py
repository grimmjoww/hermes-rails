"""Rails' d3 layer: the decision models behind Rails' tools, and where each question goes.

Three local layers, each with its own on/off server:
  eyes  - "Computer Use Vision": d3-turbo-INT4 on :8899. Screenshots, images, video, Box and Track questions.
  smart - "Decision Second Opinion": d3-ultra 4-bit on :8898. Text decisions; used as a second opinion when d1 is unsure.
  fast  - "Decision Guard": d1-3B on :8089. Text only, ~0.2 s; first stop for auto text, and for the gates.

use="auto" on text asks d1 first and escalates to d3 when d1 is unsure (same bands as the gates).
If the state itself says the fact is missing, auto returns a calibrated mid/unknown answer and does
not ask d3 to guess. Pictures and video go straight to the eyes. use="fast" is d1 only; use="smart" forces d3.

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
import re
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
VISIBLE_TRUST_AT = 0.90  # if "is it there?" is at least this sure and the box has real on-screen area,
                         # trust the box even when confidence is 0 (d3 sometimes returns 0 with a good box)
MIN_BOX_PX = 2        # a usable box must have at least this many pixels of width and height inside the image
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


# Same bands the scope/workflow gates use in ask_turn.
UNSURE_CHOICE_AT = 0.60  # a d1 pick below this is unsure
UNSURE_NOUL = (0.30, 0.70)  # a yes/no inside this band is unsure
# A cheap pre-filter, not an extra d1 question: only when the STATE itself says the fact is missing.
# Auto then returns a calibrated mid answer (noul 0.5) and does not let d3 guess.
# Kept names so older logs still make sense; the per-request answerable question is no longer attached.
ANSWERABLE_KEY = "_rails_answerable"
UNKNOWN_AT = 0.60
UNKNOWN_NOUL = 0.50
UNKNOWN_BY = "Rails (unknown: fact missing from the state)"
MISSING_FACT = re.compile(
    r"(?is)("
    r"you have no other information|"
    r"we have no other information|"
    r"nobody has told you|"
    r"no one has told you|"
    r"you have not been told|"
    r"you have not been given|"
    r"you have not opened|"
    r"you (do not|don't) know what is inside|"
    r"have not been told what is inside|"
    r"has not happened yet|"
    r"has not occurred yet|"
    r"you have not seen the (code|repro|file|document|result|numbers?|contents?)|"
    r"you have not seen the code or a repro|"
    r"you have not seen a repro"
    r")"
)


def unsure(answers: dict) -> list[str]:
    """The questions d1 was unsure about (low confidence, an 'unclear' pick, or an error)."""
    out = []
    for name, a in (answers or {}).items():
        if not isinstance(a, dict):
            continue
        if a.get("error"):
            out.append(name)
            continue
        if a.get("type") == "choice":
            pick = str(a.get("choice") or "")
            p = float((a.get("probabilities") or {}).get(a.get("choice"), 0) or 0)
            if p < UNSURE_CHOICE_AT or pick.lower() == "unclear":
                out.append(name)
        elif a.get("type") == "noul" and UNSURE_NOUL[0] < float(a.get("noul", 0.5)) < UNSURE_NOUL[1]:
            out.append(name)
    return out


def _d1_body(body: dict) -> dict:
    return {"state": body.get("state"), "questions": body.get("questions")}


def fact_missing_from_state(state: str) -> bool:
    """True only when the given state itself says the needed fact is missing or unknowable."""
    return bool(state) and bool(MISSING_FACT.search(str(state)))


def unknown_answers(questions: dict) -> dict:
    """Calibrated mid/unknown answers so a missing fact is not turned into a confident guess."""
    out = {}
    for name, q in (questions or {}).items():
        if name == ANSWERABLE_KEY or not isinstance(q, dict):
            continue
        typ = q.get("type")
        if typ == "noul":
            out[name] = {"type": "noul", "noul": UNKNOWN_NOUL, "unknown": True}
        elif typ == "choice":
            criteria = q.get("criteria") or {}
            keys = list(criteria) if isinstance(criteria, dict) else []
            pick = "unknown" if "unknown" in keys else ("unclear" if "unclear" in keys else None)
            if pick:
                rest = [k for k in keys if k != pick]
                share = (0.10 / len(rest)) if rest else 0.0
                probs = {k: (0.90 if k == pick else share) for k in keys} or {pick: 1.0}
                out[name] = {"type": "choice", "choice": pick, "probabilities": probs, "unknown": True}
            else:
                out[name] = {"type": "choice", "choice": "unclear", "probabilities": {"unclear": 1.0},
                             "unknown": True}
        else:
            continue
    return out


def _unknown_result(body: dict, use: str, ms: int = 0) -> dict:
    answers = unknown_answers(body.get("questions") or {})
    by = {name: UNKNOWN_BY for name in answers}
    return {"answered_by": UNKNOWN_BY, "ms": ms, "answers": answers,
            "routing": {"use": use, "escalated": [], "unanswerable": True,
                        "unknown_from_state": True, "d1_ms": 0, "d3_ms": 0, "by": by}}


def _needs_full_d3(body: dict) -> bool:
    """Inputs d1 cannot take: pictures, video, several states, or long-context options."""
    return bool(needs_eyes(body) or body.get("states") or body.get("assets") or body.get("options"))


def d3_text_layer() -> str | None:
    """Where a text question escalates or a use='smart' call goes: d3-ultra if up, else d3-turbo."""
    if ready(SMART):
        return SMART
    if ready(EYES):
        return EYES
    return None


def pick_layer(body: dict, use: str = "auto") -> str:
    """Which single layer answers a forced or media request.
    Media and Box/Track always use the eyes. use='fast' is d1 only. use='smart' forces d3
    (ultra if it is up, otherwise turbo). Auto text-only is handled in decide() (d1 first).
    Raises D3Error naming the shortcut to turn on when nothing suitable is up."""
    use = (use or "auto").lower()
    if needs_eyes(body) or use == "vision":
        if not ready(EYES):
            raise D3Error("pictures and video need Computer Use Vision (d3-turbo at 127.0.0.1:8899). Start that server (it takes about "
                          "5 minutes to load)")
        return EYES
    if use == "fast":
        if _needs_full_d3(body):
            raise D3Error("this question needs d3 (pictures, video, several inputs or long text). Start "
                          "Computer Use Vision (d3-turbo on port 8899) or Decision Second Opinion (d3-ultra on port 8898)")
        if not ready(FAST):
            raise D3Error("no decision model is on. Start Decision Guard (d1 on port 8089); for pictures start Computer Use Vision (d3-turbo on port 8899)")
        return FAST
    if use == "smart" or _needs_full_d3(body):
        layer = d3_text_layer()
        if layer:
            return layer
        raise D3Error("this question needs d3 (pictures, video, several inputs, long text, or use='smart'). "
                      "Start Computer Use Vision (d3-turbo on port 8899) or Decision Second Opinion (d3-ultra on port 8898)")
    if ready(FAST):
        return FAST
    layer = d3_text_layer()
    if layer:
        return layer
    raise D3Error("no decision model is on. Start Decision Guard (d1 on port 8089); for pictures start Computer Use Vision (d3-turbo on port 8899)")


def _result(base: str, reply: dict, ms: int, use: str, extra: dict | None = None) -> dict:
    answers = reply.get("answers", {})
    out = {"answered_by": LAYER_NAMES[base], "ms": ms, "answers": answers,
           "routing": {"use": use, "by": {name: LAYER_NAMES[base] for name in answers}}}
    if base == FAST:
        out["routing"]["d1_ms"] = ms
    else:
        out["routing"]["d3_ms"] = ms
    if extra:
        out["routing"].update(extra)
    for key in ("states", "usage"):
        if key in reply:
            out[key] = reply[key]
    return out


def _ask_layer(base: str, body: dict, use: str) -> dict:
    started = time.perf_counter()
    reply = ask(base, body if base != FAST else _d1_body(body))
    return _result(base, reply, round((time.perf_counter() - started) * 1000), use)


def decide(body: dict, use: str = "auto") -> dict:
    """Answer on the right layer -> {answered_by, ms, answers, routing, states?, usage}.

    Auto text: if the state itself says the fact is missing, return a calibrated mid
    answer (noul 0.5 / unknown) without calling d3. Otherwise ask d1 first and escalate
    to d3 only when d1 is unsure (same bands as the gates). Pictures and use='smart'
    go straight to d3. use='fast' stays on d1. routing.by names the model that answered
    each question; d1_ms / d3_ms are the request times.
    """
    use = (use or "auto").lower()
    if use == "auto" and not _needs_full_d3(body):
        if fact_missing_from_state(str(body.get("state") or "")):
            return _unknown_result(body, use)
        if not ready(FAST):
            layer = d3_text_layer()
            if not layer:
                raise D3Error("no decision model is on. Start 'Decision Guard' "
                              "(and for pictures 'Computer Use Vision')")
            return _ask_layer(layer, body, use)
        started = time.perf_counter()
        reply = ask(FAST, _d1_body(body))
        d1_ms = round((time.perf_counter() - started) * 1000)
        answers = dict(reply.get("answers") or {})
        by = {name: LAYER_NAMES[FAST] for name in answers}
        out = {"answered_by": LAYER_NAMES[FAST], "ms": d1_ms, "answers": answers,
               "routing": {"use": "auto", "d1_ms": d1_ms, "escalated": [], "by": by,
                           "unanswerable": False, "unknown_from_state": False,
                           "d1_answers": {name: (a.get("choice") if a.get("type") == "choice"
                                                 else a.get("noul"))
                                          for name, a in answers.items() if isinstance(a, dict)}}}
        if "usage" in reply:
            out["usage"] = reply["usage"]
        doubt = unsure(answers)
        if not doubt:
            return out
        layer = d3_text_layer()
        if not layer:
            out["routing"]["skipped"] = "d3 is off; kept the Decision Guard answer"
            return out
        started = time.perf_counter()
        try:
            second = ask(layer, body)
        except Exception as error:  # noqa: BLE001 - keep d1, fail open, loudly
            out["routing"]["error"] = f"{type(error).__name__}: {error}"[:160]
            return out
        d3_ms = round((time.perf_counter() - started) * 1000)
        second_answers = second.get("answers") or {}
        replaced = []
        for name in doubt:
            a = second_answers.get(name)
            if isinstance(a, dict) and "error" not in a:
                answers[name] = a
                by[name] = LAYER_NAMES[layer]
                replaced.append(name)
        out["routing"]["d3_ms"] = d3_ms
        out["routing"]["escalated"] = replaced
        out["routing"]["by"] = by
        out["ms"] = d1_ms + d3_ms
        if replaced and len(replaced) < len(answers):
            out["answered_by"] = f"{LAYER_NAMES[FAST]}, then {LAYER_NAMES[layer]} for {', '.join(replaced)}"
        elif replaced:
            out["answered_by"] = LAYER_NAMES[layer]
        if "usage" in second:
            out["usage"] = second["usage"]
        return out
    return _ask_layer(pick_layer(body, use), body, use)


def box_in_pixels(answer: dict, width: int, height: int) -> dict:
    """A Box answer (fractions) -> pixel edges and the centre to click, in the image's own pixels."""
    b = answer.get("box") or {}
    left, right = round(b.get("left", 0) * width), round(b.get("right", 0) * width)
    top, bottom = round(b.get("top", 0) * height), round(b.get("bottom", 0) * height)
    return {"box_px": {"left": left, "top": top, "right": right, "bottom": bottom},
            "click_at": [round((left + right) / 2), round((top + bottom) / 2)],
            "confidence": round(float(answer.get("confidence", 0.0)), 3)}


def box_is_usable(box_px: dict, width: int, height: int) -> bool:
    """True when the box overlaps the image with real area, not a zero-size or fully off-screen rect."""
    if not isinstance(box_px, dict) or width <= 0 or height <= 0:
        return False
    left, top = box_px.get("left", 0), box_px.get("top", 0)
    right, bottom = box_px.get("right", 0), box_px.get("bottom", 0)
    try:
        w = min(float(right), width) - max(float(left), 0)
        h = min(float(bottom), height) - max(float(top), 0)
    except (TypeError, ValueError):
        return False
    return w >= MIN_BOX_PX and h >= MIN_BOX_PX


def target_found(visible: float, confidence: float, box_px: dict, width: int, height: int) -> bool:
    """A hit: the target is visible, and either the box is confident enough or (when visible_p is
    high) the box still has real on-screen area, even if confidence came back 0."""
    try:
        visible = float(visible)
        confidence = float(confidence)
    except (TypeError, ValueError):
        return False
    if visible < FOUND_AT:
        return False
    if not box_is_usable(box_px, width, height):
        return False
    if confidence >= BOX_AT:
        return True
    return visible >= VISIBLE_TRUST_AT

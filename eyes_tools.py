"""The d3 tools Rails gives the agent: decide, screen_find, screen_check, video_check (see eyes.py for the layers).

Every call is one line in Rails' log (kind "d3_tool"); a layer that is off is reported in plain words naming the
server to start (d1 on 8089, d3-turbo on 8899, d3-ultra on 8898). Tool results are JSON text.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

try:
    from . import eyes
except ImportError:  # loaded by file path (tests): load the sibling the same way
    _spec = importlib.util.spec_from_file_location("rails_eyes", Path(__file__).with_name("eyes.py"))
    eyes = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(eyes)

TOOLSET = "decisions"
_QTYPES = "noul (yes/no), choice (pick one), score (rate on levels), box (where in the picture), track (follow in video)"


def _result(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _media_list(paths: Any) -> list[str]:
    if not paths:
        return []
    if isinstance(paths, str):
        paths = [paths]
    return [eyes.data_url(p) for p in paths]


def _assets(mapping: Any) -> dict:
    out = {}
    for name, path in (mapping or {}).items():
        data, kind = eyes._media(path)
        if not kind.startswith("image/"):
            raise eyes.D3Error(f"asset {name!r} must be an image")
        out[name] = {"type": "image", "data_base64": eyes.base64.b64encode(data).decode()}
    return out


def _states(items: Any) -> list[dict]:
    out = []
    for item in items or []:
        entry = {"state": item.get("state"), "questions": item.get("questions") or {}}
        if item.get("images"):
            entry["images"] = _media_list(item["images"])
        if item.get("videos"):
            entry["videos"] = _media_list(item["videos"])
        out.append(entry)
    return out


def _pixels(answers: dict, size: tuple[int, int] | None) -> dict:
    """Add pixel boxes and a click point to every Box answer, when we know the picture's size."""
    if not size:
        return answers
    for answer in answers.values():
        if isinstance(answer, dict) and answer.get("type") == "box" and "box" in answer:
            answer.update(eyes.box_in_pixels(answer, *size))
    return answers


def make_handlers(log: Callable[[dict], None]) -> dict[str, Callable[..., str]]:
    def run(tool: str, fn: Callable[[], dict]) -> str:
        try:
            out = fn()
            entry = {"kind": "d3_tool", "tool": tool, "by": out.get("answered_by"), "ms": out.get("ms")}
            routing = out.get("routing") or {}
            for key in ("use", "d1_ms", "d3_ms", "escalated", "skipped"):
                if key in routing:
                    entry[key] = routing[key]
            if routing.get("by"):
                entry["by_question"] = routing["by"]
            log(entry)
            return _result(out)
        except eyes.D3Error as error:
            log({"kind": "d3_tool", "tool": tool, "error": str(error)[:200]})
            return _result({"error": str(error)})
        except Exception as error:  # noqa: BLE001 - never crash the turn; say what broke
            log({"kind": "d3_tool", "tool": tool, "error": f"{type(error).__name__}: {error}"[:200]})
            return _result({"error": f"{type(error).__name__}: {error}"})

    def decide(args: dict, **_: Any) -> str:
        def go() -> dict:
            body: dict[str, Any] = {"state": args.get("state", ""), "questions": args.get("questions") or {}}
            if args.get("images"):
                body["images"] = _media_list(args["images"])
            if args.get("videos"):
                body["videos"] = _media_list(args["videos"])
            if args.get("assets"):
                body["assets"] = _assets(args["assets"])
            if args.get("states"):
                body["states"] = _states(args["states"])
            if args.get("long_context"):
                body["options"] = {"long_context": True}
            out = eyes.decide(body, str(args.get("use") or "auto"))
            images = args.get("images")
            if isinstance(images, str):
                images = [images]
            if images and len(images) == 1 and not args.get("videos"):
                _pixels(out["answers"], eyes.image_size(images[0]))
            return out
        return run("decide", go)

    def find_many(targets: list[str], args: dict) -> dict:
        """Several targets, one read of the screenshot: a Box plus a yes/no 'is it there' for each."""
        image = args.get("image_path") or eyes.latest_screenshot()
        questions = {}
        for i, target in enumerate(targets):
            questions[f"where{i}"] = {"type": "box", "instructions": target}
            questions[f"there{i}"] = {"type": "noul", "instructions": f"Is this in the picture: {target}?"}
        body = {"state": args.get("context") or "A screenshot of a computer screen.",
                "images": [eyes.data_url(image)], "questions": questions}
        out = eyes.decide(body, "vision")
        size = eyes.image_size(image)
        found_all, results = True, []
        for i, target in enumerate(targets):
            spot = eyes.box_in_pixels(out["answers"][f"where{i}"], *size)
            visible = round(float(out["answers"][f"there{i}"].get("noul", 0.0)), 3)
            found = eyes.target_found(visible, spot["confidence"], spot["box_px"], *size)
            found_all = found_all and found
            results.append({"target": target, "found": found, "visible_p": visible,
                            **({"click_at": spot["click_at"], "box_px": spot["box_px"]} if found else {}),
                            "confidence": spot["confidence"]})
        return {"answered_by": out["answered_by"], "ms": out["ms"], "found": found_all, "targets": results,
                "image": str(image), "image_size": list(size),
                "advice": ("Click each found target's click_at in order, exactly as given (already in the capture's "
                           "coordinates), without a new capture in between unless the page changed. Skip any that "
                           "are not found.")}

    def screen_find(args: dict, **_: Any) -> str:
        def go() -> dict:
            many = [str(t).strip() for t in (args.get("targets") or []) if str(t).strip()]
            if many:
                return find_many(many[:32], args)
            target = str(args.get("target") or "").strip()
            if not target:
                raise eyes.D3Error("say what to find, e.g. target='the Save button in the toolbar'")
            image = args.get("image_path") or eyes.latest_screenshot()
            body = {"state": args.get("context") or "A screenshot of a computer screen.",
                    "images": [eyes.data_url(image)],
                    "questions": {"where": {"type": "box", "instructions": target},
                                  "there": {"type": "noul", "instructions": f"Is this in the picture: {target}?"}}}
            out = eyes.decide(body, "vision")
            where, there = out["answers"]["where"], out["answers"]["there"]
            size = eyes.image_size(image)
            spot = eyes.box_in_pixels(where, *size)
            visible = round(float(there.get("noul", 0.0)), 3)
            # High visibility + a degenerate box: one retry, in case the first box was empty.
            if (visible >= eyes.VISIBLE_TRUST_AT and not eyes.box_is_usable(spot["box_px"], *size)):
                retry = eyes.decide(body, "vision")
                out["ms"] = out.get("ms", 0) + retry.get("ms", 0)
                where, there = retry["answers"]["where"], retry["answers"]["there"]
                spot = eyes.box_in_pixels(where, *size)
                visible = round(float(there.get("noul", 0.0)), 3)
            found = eyes.target_found(visible, spot["confidence"], spot["box_px"], *size)
            if not found:
                advice = "Not found: do not click. Scroll, open the right view, capture again, or describe it differently."
            elif spot["confidence"] < eyes.SURE_BOX_AT:
                advice = "Found, but not sure of the exact spot: after clicking, confirm with screen_check."
            else:
                advice = ("Found. Click click_at with computer_use(action='click', coordinate=click_at) exactly as given: it "
                          "is already in the capture's coordinates, so do not scale it. Then capture and screen_check.")
            return {"answered_by": out["answered_by"], "ms": out["ms"], "found": found, "visible_p": visible,
                    **spot, "image": str(image), "image_size": list(size), "advice": advice}
        return run("screen_find", go)

    def screen_check(args: dict, **_: Any) -> str:
        def go() -> dict:
            image = args.get("image_path") or eyes.latest_screenshot()
            body = {"state": args.get("context") or "A screenshot of a computer screen.",
                    "images": [eyes.data_url(image)], "questions": args.get("questions") or {}}
            out = eyes.decide(body, "vision")
            _pixels(out["answers"], eyes.image_size(image))
            out["image"] = str(image)
            return out
        return run("screen_check", go)

    def video_check(args: dict, **_: Any) -> str:
        def go() -> dict:
            video = args.get("video_path")
            if not video:
                raise eyes.D3Error("pass video_path (MP4, WebM, MOV or MKV, up to 300 s and 32 MB)")
            body = {"state": args.get("context") or "A video clip.", "videos": [eyes.data_url(video)],
                    "questions": args.get("questions") or {}}
            return eyes.decide(body, "vision")
        return run("video_check", go)

    return {"decide": decide, "screen_find": screen_find, "screen_check": screen_check, "video_check": video_check}


def _q(desc: str) -> dict:
    return {"type": "object", "description": desc}


SCHEMAS = {
    "decide": {
        "name": "decide",
        "description": (
            "Ask the local decision models typed questions and get a probability for every answer (no text "
            "generation; free and private). Types: " + _QTYPES + ". Use it to judge, sort, rank, check or choose: "
            "is this a scam, which job fits best (score), which file/option/screenshot matches, is this draft good "
            "enough, does this log show the error. Put ALL questions about one input in one call. Text is asked "
            "of d1 first (fast, ~0.2 s) and only escalated to d3 if d1 is unsure. For a hard judgment call, pass "
            "use='smart' to send the question straight to d3. Pictures, videos, box and track go straight to "
            "d3-turbo (eyes). The result names which model answered each question and how many ms it took."),
        "parameters": {"type": "object", "properties": {
            "state": {"description": "The input to judge: text, or a JSON object/array. Keep it to what matters."},
            "questions": _q("{name: {type, instructions, criteria?}}. choice: criteria {option: short description}; "
                            "score: criteria [levels, lowest first]; noul: criteria optional {true, false}; box: "
                            "instructions name the thing ('the leftmost Save button'), optional 'at' seconds for "
                            "video; track: instructions + rate 1 or 2 per second."),
            "images": {"type": "array", "items": {"type": "string"}, "description": "Local image paths every question sees."},
            "videos": {"type": "array", "items": {"type": "string"}, "description": "Local video paths (<=300 s, 32 MB)."},
            "assets": _q("Images only one question or option sees: {'<picture 1>': 'C:/path.png'}; write the "
                         "placeholder inside that question's instructions or criteria."),
            "states": {"type": "array", "items": {"type": "object"},
                       "description": "Several separate inputs in one call, each {state, questions, images?, videos?} "
                                      "(e.g. 20 job posts, each scored)."},
            "long_context": {"type": "boolean", "description": "Allow very long input (slower)."},
            "use": {"type": "string", "enum": ["auto", "smart", "vision", "fast"],
                    "description": "auto (default): text goes to d1 first and escalates to d3 only if d1 is "
                                   "unsure; pictures go to d3. smart = send text straight to d3 (use this for "
                                   "a hard judgment call). vision = d3-turbo (eyes). fast = d1 only."},
        }, "required": ["state", "questions"]},
    },
    "screen_find": {
        "name": "screen_find",
        "description": (
            "Find something on the screen and get the point to click. Use when computer_use's numbered elements "
            "do not include the target (games, canvases, unlabeled web apps). Reads the latest computer_use capture "
            "unless image_path is given, and checks the thing is really there. Returns found, click_at [x, y] in "
            "that screenshot's pixels (pass to computer_use click as coordinate exactly as given, never rescaled), the box "
            "and its confidence."),
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "What to find, said plainly and specifically: "
                                                        "'the blue Save button at the bottom of the dialog'."},
            "targets": {"type": "array", "items": {"type": "string"},
                        "description": "Several things to find on the same screenshot in ONE d3 call (up to 32), "
                                       "e.g. ['the address bar', 'the search box', 'the first search result link']. "
                                       "Each gets found, click_at and confidence."},
            "image_path": {"type": "string", "description": "Optional screenshot path; default the latest capture."},
            "context": {"type": "string", "description": "Optional one line on what app/screen this is."},
        }, "required": []},
    },
    "screen_check": {
        "name": "screen_check",
        "description": (
            "Ask questions about the screen (or any image) in one call: did the click work, is a dialog open, is it "
            "still loading, which of these is selected, how far along is the progress bar. Use after every "
            "computer_use action that should change the screen. Types: " + _QTYPES + " (box answers include "
            "click_at). Reads the latest computer_use capture unless image_path is given."),
        "parameters": {"type": "object", "properties": {
            "questions": _q("{name: {type, instructions, criteria?}}, as in decide."),
            "image_path": {"type": "string", "description": "Optional image path; default the latest capture."},
            "context": {"type": "string", "description": "Optional one line on what the picture is."},
        }, "required": ["questions"]},
    },
    "video_check": {
        "name": "video_check",
        "description": (
            "Exact answers about a video (a screen recording, a game test run, a clip): yes/no, choice and score "
            "questions, a box at a given second ('at'), or a track following something through the clip "
            "('rate' 1 or 2 per second) with whether it is visible. For a plain description of a video use "
            "video_analyze instead."),
        "parameters": {"type": "object", "properties": {
            "video_path": {"type": "string", "description": "Local MP4, WebM, MOV or MKV, up to 300 s and 32 MB."},
            "questions": _q("{name: {type, instructions, criteria?, at?, rate?}}."),
            "context": {"type": "string", "description": "Optional one line on what the clip is."},
        }, "required": ["video_path", "questions"]},
    },
}

EMOJI = {"decide": "⚖", "screen_find": "🎯", "screen_check": "👁", "video_check": "🎞"}


def register_tools(ctx, log: Callable[[dict], None]) -> None:
    handlers = make_handlers(log)
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name],
                          description=schema["description"][:120], emoji=EMOJI[name])

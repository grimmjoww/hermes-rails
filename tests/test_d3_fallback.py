"""text_first falls back to d3-turbo when d1 cannot aim. vision does not."""
import json

from test_eyes import FOUND, _cua_setup, _do
from test_text_first import CAPTURE, SCREEN, _choice, _text

EMPTY = "capture mode=som 1456x869"
CANVAS = json.dumps({"elements": [
    {"index": 1, "role": "AXCanvas", "label": "game view", "bounds": [0, 0, 800, 600]},
]})


def _capture(ctx, result):
    assert ctx.hooks["pre_tool_call"](tool_name="computer_use", args=CAPTURE, session_id="s", turn_id="t1") is None
    return ctx.hooks["transform_tool_result"](tool_name="computer_use", args=CAPTURE, session_id="s", result=result)


def test_an_unsure_d1_escalates_to_d3_aim(tmp_path, monkeypatch):
    rails, ctx = _text(tmp_path, monkeypatch, lambda state, questions: _choice("6", 0.59, ("none",)))
    note = _capture(ctx, SCREEN)
    assert "⚖ d1 unsure → d3 aim" in ctx.shown
    assert "screen_find" in note
    assert "d1_pick" not in rails._CUA["s"]
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held["action"] == "block" and "screen_find" in held["message"]
    assert _do(ctx, "screen_find", {"target": "Add to cart"}, FOUND) is None
    assert rails._CUA["s"].get("d3_decided") is True
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [399, 202]}, '{"ok": true}') is None


def test_an_empty_list_and_a_canvas_escalate_to_d3_aim(tmp_path, monkeypatch):
    def unused(state, questions):
        raise AssertionError("d1 should not be asked when nothing is clickable")

    rails, ctx = _text(tmp_path, monkeypatch, unused)
    note = _capture(ctx, EMPTY)
    assert "⚖ no text list → d3 aim" in ctx.shown
    assert "screen_find" in note
    assert _do(ctx, "computer_use", {"action": "click", "element": 6})["action"] == "block"
    canvas = _capture(ctx, CANVAS)
    assert "⚖ canvas → d3 aim" in ctx.shown
    assert "screen_find" in canvas
    assert not any(entry.get("reason") == "picked" for entry in _logs(rails))


def _logs(rails):
    if not rails.LOG.exists():
        return []
    return [json.loads(line) for line in rails.LOG.read_text(encoding="utf-8").splitlines()]


def test_d3_down_fails_open_with_a_plain_message(tmp_path, monkeypatch):
    rails, ctx = _text(tmp_path, monkeypatch, lambda state, questions: _choice("6", 0.59, ("none",)), vision=False)
    note = _capture(ctx, SCREEN)
    assert "⚖ d3 aim unavailable" in ctx.shown
    assert "not running" in note and "will not hold" in note
    assert "Tell the user plainly" in note
    skipped = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert not (skipped and "computer-use gate" in (skipped or {}).get("message", ""))


def test_text_first_end_of_turn_screen_check_still_runs(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, ask="use computer use to open the Recycle Bin")
    rails.CUA_DRIVER = "text_first"
    seen = []
    monkeypatch.setattr(rails, "visual_check", lambda ask, at: seen.append(ask) or {
        "result": "done", "why": "looks finished", "ask_agent": "", "p_done": 0.91, "p_error": 0.0})
    ctx.hooks["pre_tool_call"](tool_name="computer_use", args={"action": "type", "text": "x"},
                               session_id="s", turn_id="t1")
    ctx.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert seen == ["use computer use to open the Recycle Bin"]
    assert any(line.startswith("✅ screen check") for line in ctx.shown)

    rails_down, ctx_down, steer_down = _cua_setup(tmp_path, monkeypatch, ask="use computer use to open the Recycle Bin")
    rails_down.CUA_DRIVER = "text_first"
    monkeypatch.setattr(rails_down, "visual_check", lambda ask, at: {
        "result": "unavailable", "why": "Computer Use Vision is off, screen not checked", "ask_agent": "say so"})
    ctx_down.hooks["pre_tool_call"](tool_name="computer_use", args={"action": "type", "text": "x"},
                                    session_id="s", turn_id="t1")
    ctx_down.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert any("screen check unavailable" in line for line in ctx_down.shown)


def test_vision_default_does_not_escalate(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch)
    assert rails.CUA_DRIVER == "vision"
    assert _do(ctx, "computer_use", CAPTURE, EMPTY) is None
    assert _do(ctx, "computer_use", CAPTURE, SCREEN) is None
    assert not any("d3 aim" in line or "d1 picked" in line for line in ctx.shown)
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held["action"] == "block" and "screen_find" in held["message"]

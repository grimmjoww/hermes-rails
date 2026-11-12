"""text_first: d1 picks one numbered element. vision (the default) does not ask."""
import json

from test_eyes import _cua_setup, _do
from test_rails import load

CART = {"index": 6, "role": "AXButton", "label": "Add to cart", "bounds": [412, 300, 108, 36], "app": "chrome"}
SEARCH = {"index": 7, "role": "AXTextField", "label": "Search", "bounds": None, "app": "chrome"}
SCREEN = json.dumps({"mode": "som", "elements": [CART, SEARCH]})
CAPTURE = {"action": "capture", "mode": "som"}
TAG = '⚖ d1 picked [6] button "Add to cart" 0.82'


def _choice(choice, p, others):
    rest = (1 - p) / len(others) if others else 0
    return {"type": "choice", "choice": choice,
            "probabilities": {choice: p, **{name: rest for name in others}}}


def _logs(rails):
    if not rails.LOG.exists():
        return []
    return [json.loads(line) for line in rails.LOG.read_text(encoding="utf-8").splitlines()]


def _text(tmp_path, monkeypatch, reply, vision=True):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, vision=vision)
    rails.CUA_DRIVER = "text_first"

    def ask(state, questions):
        if "target" not in questions:
            raise AssertionError(questions)
        found = reply(state, questions)
        if isinstance(found, dict) and "target" not in found and found.get("type") == "choice":
            found = {"target": found}
        return found

    rails.ask_d1 = ask
    return rails, ctx


def test_vision_driver_does_not_ask_d1_or_change_the_click(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch)
    assert rails.CUA_DRIVER == "vision"
    asked = []
    rails.ask_d1 = lambda state, questions: asked.append(questions)
    assert _do(ctx, "computer_use", CAPTURE, SCREEN) is None
    assert asked == []
    assert not any("d1 picked" in line for line in ctx.shown)
    assert not any(entry.get("kind") == "text_first" for entry in _logs(rails))
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held["action"] == "block" and "screen_find" in held["message"]


def test_a_confident_pick_backs_that_element_and_a_point_inside_its_box(tmp_path, monkeypatch):
    rails, ctx = _text(tmp_path, monkeypatch, lambda state, questions: _choice("6", 0.82, ("7", "none")))
    assert ctx.hooks["pre_tool_call"](tool_name="computer_use", args=CAPTURE, session_id="s", turn_id="t1") is None
    note = ctx.hooks["transform_tool_result"](tool_name="computer_use", args=CAPTURE, session_id="s", result=SCREEN)
    assert TAG in ctx.shown
    assert TAG in note and "Click that element" in note
    assert rails._CUA["s"]["d1_pick"]["index"] == 6
    assert rails._CUA["s"].get("d3_decided") is False
    assert _do(ctx, "computer_use", {"action": "click", "element": 6}, '{"ok": true}') is None
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [450, 320]}, '{"ok": true}') is None
    other = _do(ctx, "computer_use", {"action": "click", "element": 7})
    assert other["action"] == "block"
    outside = _do(ctx, "computer_use", {"action": "click", "coordinate": [1, 1]})
    assert outside["action"] == "block"
    assert any(entry.get("kind") == "text_first" and entry.get("reason") == "picked" for entry in _logs(rails))
    # a new screenshot drops the pick
    assert _do(ctx, "computer_use", CAPTURE, "capture mode=som 1456x869") is None
    assert "d1_pick" not in rails._CUA["s"]
    assert _do(ctx, "computer_use", {"action": "click", "element": 6})["action"] == "block"


def test_an_unsure_pick_does_not_back_the_click(tmp_path, monkeypatch):
    rails, ctx = _text(tmp_path, monkeypatch, lambda state, questions: _choice("6", 0.59, ("none",)))
    assert _do(ctx, "computer_use", CAPTURE, SCREEN) is None
    assert not any("d1 picked" in line for line in ctx.shown)
    assert "d1_pick" not in rails._CUA["s"]
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held["action"] == "block" and "screen_find" in held["message"]
    assert any(entry.get("kind") == "text_first" and entry.get("reason") == "unsure" for entry in _logs(rails))


def test_d1_down_fails_open_to_todays_behavior(tmp_path, monkeypatch):
    def down(state, questions):
        raise OSError("connection refused")

    rails, ctx = _text(tmp_path, monkeypatch, down, vision=True)
    assert _do(ctx, "computer_use", CAPTURE, SCREEN) is None
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held["action"] == "block" and "screen_find" in held["message"]
    assert any(entry.get("kind") == "text_first" and entry.get("reason") == "down" for entry in _logs(rails))
    assert not any("d1 picked" in line for line in ctx.shown)

    rails_off, ctx_off = _text(tmp_path, monkeypatch, down, vision=False)
    assert _do(ctx_off, "computer_use", CAPTURE, SCREEN) is None
    skipped = _do(ctx_off, "computer_use", {"action": "click", "element": 6})
    assert not (skipped and "computer-use gate" in (skipped or {}).get("message", ""))
    assert any(entry.get("kind") == "text_first" and entry.get("reason") == "down" for entry in _logs(rails_off))


def test_an_empty_list_is_logged_and_the_click_stays_held(tmp_path, monkeypatch):
    def unused(state, questions):
        raise AssertionError("d1 should not be asked when the list is empty")

    rails, ctx = _text(tmp_path, monkeypatch, unused)
    assert _do(ctx, "computer_use", CAPTURE, "capture mode=som 1456x869") is None
    assert any(entry.get("kind") == "text_first" and entry.get("reason") == "no text list" for entry in _logs(rails))
    assert _do(ctx, "computer_use", {"action": "click", "element": 6})["action"] == "block"


def test_default_driver_constant_is_vision(tmp_path):
    rails = load(tmp_path)
    assert rails.CUA_DRIVER == "vision"

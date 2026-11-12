"""cloud driver: the model clicks on its own. vision stays the default. money and email are still checked."""
from test_eyes import _cua_setup, _do
from test_money_email import _install
from test_rails import Ctx, fake_d1, load
from test_settings import SettingsCtx

TAG = "⚖ cloud driver: clicks unheld, money/email still checked"


def _cloud(tmp_path, monkeypatch, vision=True, ask="can you use computer use and navigate to amazon"):
    rails, ctx = load(tmp_path), Ctx()
    rails.CUA_MODE = "on"
    rails.CUA_DRIVER = "cloud"
    rails.SECOND_MODE = "off"
    rails.ask_d1 = fake_d1(kind="other")
    rails.register(ctx)
    monkeypatch.setattr(rails._eyes_tools().eyes, "ready", lambda base: vision)
    steer = ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message=ask)
    return rails, ctx, steer


def test_clicks_are_not_held_and_the_eyes_stay_optional(tmp_path, monkeypatch):
    rails, ctx, steer = _cloud(tmp_path, monkeypatch, vision=True)
    assert ctx.shown.count(TAG) == 1
    assert "will not hold a click" in steer["context"]
    assert "holds any click" not in steer["context"]
    assert "screen_find" in steer["context"] and "decide" in steer["context"] and "screen_check" in steer["context"]
    assert _do(ctx, "computer_use", {"action": "click", "element": 6}) is None
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [10, 10]}) is None
    assert _do(ctx, "screen_find", {"target": "the search box"}) is None
    assert _do(ctx, "decide", {"state": "x", "questions": {}}) is None
    assert _do(ctx, "screen_check", {"state": "x"}) is None
    assert ctx.shown.count(TAG) == 1
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t2", user_message="use computer use to open the Recycle Bin")
    assert ctx.shown.count(TAG) == 2


def test_cloud_mode_cannot_bypass_the_money_email_check(tmp_path, monkeypatch):
    rails, ctx, _steer = _cloud(tmp_path, monkeypatch, vision=True)
    _install(rails, 0.12)
    held = _do(ctx, "computer_use", {"action": "click", "target": "Checkout"})
    assert held["action"] == "block" and "Confirm with the user" in held["message"]
    assert "screen_find" not in held["message"]
    assert any(line == "⛔ d1 money check: not authorized" for line in ctx.shown)

    _install(rails, 0.50)
    unsure = _do(ctx, "computer_use", {"action": "click", "target": "Pay now"})
    assert unsure["action"] == "block" and "Confirm with the user" in unsure["message"]

    _install(rails, boom=True)
    down = _do(ctx, "computer_use", {"action": "click", "target": "Subscribe"})
    assert down["action"] == "block" and "D1 is unavailable" in down["message"]

    _install(rails, 0.91)
    assert _do(ctx, "computer_use", {"action": "click", "target": "Checkout"}) is None
    assert any(line == "✅ d1 money check 0.91" for line in ctx.shown)


def test_the_default_driver_is_still_vision(tmp_path, monkeypatch):
    rails = load(tmp_path)
    assert rails.CUA_DRIVER == "vision"
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("computer_use_driver:", 1)[1].split("money_email_check:", 1)[0]
    assert "cloud" in block and "text_first" in block
    assert "default: vision" in block or 'default: "vision"' in block
    rails.ask_d1 = fake_d1()
    rails.apply_settings(SettingsCtx({"computer_use_driver": "cloud"}))
    assert rails.CUA_DRIVER == "cloud"
    rails.apply_settings(SettingsCtx({"computer_use_driver": "maybe"}))
    assert rails.CUA_DRIVER == "cloud"
    fresh = load(tmp_path)
    fresh.apply_settings(SettingsCtx({"computer_use_driver": "nope"}))
    assert fresh.CUA_DRIVER == "vision"

    vision, vctx, _steer = _cua_setup(tmp_path, monkeypatch, vision=True)
    assert vision.CUA_DRIVER == "vision"
    held = _do(vctx, "computer_use", {"action": "click", "element": 6})
    assert held["action"] == "block" and "screen_find" in held["message"]


def test_the_end_of_turn_screen_check_still_runs(tmp_path, monkeypatch):
    rails, ctx, _steer = _cloud(tmp_path, monkeypatch, ask="use computer use to open the Recycle Bin")
    seen = []
    monkeypatch.setattr(rails, "visual_check", lambda ask, at: seen.append(ask) or {
        "result": "done", "why": "looks finished", "ask_agent": "", "p_done": 0.91, "p_error": 0.0})
    assert _do(ctx, "computer_use", {"action": "type", "text": "x"}) is None
    ctx.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert seen == ["use computer use to open the Recycle Bin"]
    assert any(line.startswith("✅ screen check") for line in ctx.shown)

    down, ctx_down, _steer_down = _cloud(tmp_path, monkeypatch, vision=False,
                                         ask="use computer use to open the Recycle Bin")
    monkeypatch.setattr(down, "visual_check", lambda ask, at: {
        "result": "unavailable", "why": "Computer Use Vision is off, screen not checked", "ask_agent": "say so"})
    assert _do(ctx_down, "computer_use", {"action": "click", "element": 3}) is None
    ctx_down.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert any("screen check unavailable" in line for line in ctx_down.shown)

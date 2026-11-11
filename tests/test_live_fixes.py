"""Fixes from the live end-to-end test on Willie's PC (2026-11-11, real Hermes turns, real d1 and d3)."""
from test_eyes import _cua_setup


def test_steer_drives_computer_use_with_d3_and_jev_is_optional(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch)
    text = steer["context"]
    assert "Drive computer_use" in text and "screen_find" in text and "screen_check" in text
    assert "If a jev_computer_use tool is installed" in text and "Start with jev_computer_use" not in text


def test_a_failure_lets_the_agent_drive_computer_use_itself(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch)
    note = ctx.hooks["transform_tool_result"](tool_name="jev_computer_use", args={}, session_id="s",
                                               result='{"backend": "d3_vision", "status": "blocked", "reason": "no window"}')
    assert "drive computer_use yourself" in note and "Never switch to browser use" in note
    assert "Never switch to browser use or drive computer_use yourself" not in note


def _screen_turn(tmp_path, monkeypatch, verdict):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, ask="open the Recycle Bin window on screen")
    seen = []
    monkeypatch.setattr(rails, "visual_check", lambda ask, at: seen.append(ask) or verdict)
    ctx.hooks["pre_tool_call"](tool_name="computer_use", args={"action": "double_click", "element": 1},
                               session_id="s", turn_id="t1")
    return rails, ctx, seen


def test_screen_only_turn_is_still_checked_at_the_end(tmp_path, monkeypatch):
    # Hermes fires pre_verify only after file edits, so a click-only turn used to end with no screen check.
    rails, ctx, seen = _screen_turn(tmp_path, monkeypatch, {"result": "not_done", "why": "screen doesn't look "
                                    "finished (p 0.10)", "ask_agent": "x", "p_done": 0.1, "p_error": 0.0})
    ctx.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert seen == ["open the Recycle Bin window on screen"]
    assert any(t.startswith("⚠ screen check at the end") for t in ctx.shown)
    assert '"at": "turn_end"' in rails.LOG.read_text(encoding="utf-8")


def test_turn_end_check_is_skipped_when_pre_verify_already_checked(tmp_path, monkeypatch):
    rails, ctx, seen = _screen_turn(tmp_path, monkeypatch, {"result": "done", "why": "looks finished",
                                                            "ask_agent": "", "p_done": 0.9, "p_error": 0.0})
    ctx.hooks["pre_verify"](session_id="s", attempt=0, changed_paths=[])
    ctx.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert len(seen) == 1 and any(t.startswith("✅ screen check") for t in ctx.shown)


def test_turn_end_check_off_and_no_screen_actions(tmp_path, monkeypatch):
    rails, ctx, seen = _screen_turn(tmp_path, monkeypatch, {"result": "done", "why": "", "ask_agent": "",
                                                            "p_done": 0.9, "p_error": 0.0})
    rails.VISUAL_MODE = "off"
    ctx.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert seen == []
    rails2, ctx2, steer = _cua_setup(tmp_path, monkeypatch, ask="what time is it")
    ctx2.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert not any("screen check" in t for t in ctx2.shown)


def test_jev_runs_count_as_screen_actions_for_the_turn_end_check(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, ask="use computer use to open the Recycle Bin")
    seen = []
    monkeypatch.setattr(rails, "visual_check", lambda ask, at: seen.append(at) or
                        {"result": "done", "why": "looks finished", "ask_agent": "", "p_done": 0.9, "p_error": 0.0})
    ctx.hooks["pre_tool_call"](tool_name="jev_computer_use", args={"goal": "open the Recycle Bin"},
                               session_id="s", turn_id="t1")
    ctx.hooks["post_llm_call"](session_id="s", turn_id="t1")
    assert len(seen) == 1 and seen[0] > 0 and any(t.startswith("✅ screen check") for t in ctx.shown)

"""Meaningful variants and permitted actions for the four PR #5 regressions."""
import json

import pytest

from test_eyes import FOUND, _cua_setup, _do
from test_money_email import _install
from test_money_email_regressions import PAY, _held, _result


@pytest.mark.parametrize("driver", ["vision", "text_first", "cloud"])
@pytest.mark.parametrize("case", ["find", "element", "smtp", "reply"])
@pytest.mark.parametrize("verdict", ["no", "unsure", "down", "yes", "shadow"])
def test_authorization_matrix(tmp_path, monkeypatch, driver, case, verdict):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off", ask="read the homepage")
    rails.CUA_DRIVER = driver
    states = []
    if case == "find":
        _result(ctx, "screen_find", {"target": "Pay now"}, FOUND)
        tool, args = "computer_use", {"action": "click", "coords": [399, 202]}
    elif case in {"element", "reply"}:
        label = "Reply all" if case == "reply" else "Pay now"
        _result(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [{**PAY, "label": label}]}))
        tool, args = "computer_use", {"action": "click", "element_index": "4", "target": label}
    else:
        tool, args = "shell", {"cmd": 'curl --url="SMTPS://localhost:465" --mail-rcpt=a@b -T -'}
    if verdict == "shadow":
        rails.MONEY_EMAIL_MODE = "shadow"
    original = rails.ask_d1

    def ask(state, questions):
        if "authorize" not in questions:
            return original(state, questions)
        states.append(state)
        if verdict == "down":
            raise OSError("test-only unavailable")
        return {"authorize": {"noul": {"no": 0.1, "unsure": 0.5, "yes": 0.91}.get(verdict, 0.1)}}

    rails.ask_d1 = ask
    result = ctx.hooks["pre_tool_call"](tool_name=tool, args=args, session_id="s", turn_id="t1")
    assert len(states) == 1
    if case == "find":
        assert '"screen_find_targets": ["Pay now"]' in states[0]
    if verdict in {"yes", "shadow"}:
        assert result is None
    else:
        assert result["action"] == "block" and "check]" in result["message"]


@pytest.mark.parametrize("args", [
    {"coordinate": [399, 202]}, {"x": 390, "y": 190}, {"coords": [450, 210]},
])
def test_multi_find_box_and_direct_point(tmp_path, monkeypatch, args):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    seen = _install(rails)
    _result(ctx, "screen_find", {"targets": ["Search", "Pay now"]}, json.dumps({"targets": [
        {"target": "Search", "found": True, "click_at": [20, 20]},
        {"target": "Pay now", "found": True, "click_at": [399, 202],
         "box_px": {"left": 380, "top": 180, "right": 460, "bottom": 220}},
    ]}))
    _held(ctx, "computer_use", {"action": "click", **args})
    assert seen == [["authorize"]]
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [20, 20]}) is None


def test_cache_refresh_and_session_isolation(tmp_path, monkeypatch):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off", ask="read homepage")
    seen = _install(rails)
    _result(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [PAY]}))
    _held(ctx, "computer_use", {"action": "click", "x": 399, "y": 202})
    _result(ctx, "screen_find", {"target": "Pay now"}, FOUND)
    _result(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [{**PAY, "label": "Search"}]}))
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [399, 202]}) is None
    assert _do(ctx, "computer_use", {"action": "click", "element": 4}) is None
    _result(ctx, "screen_find", {"target": "Pay now"}, FOUND)
    assert ctx.hooks["pre_tool_call"](tool_name="computer_use", args={"action": "click", "coordinate": [399, 202]},
                                      session_id="other", turn_id="t1") is None
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t2", user_message="read homepage")
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [399, 202]}) is None
    assert seen.count(["authorize"]) == 1


@pytest.mark.parametrize("label", ["Reply", "Reply all", "Forward", "Fwd"])
def test_individual_email_fields_and_cached_values(tmp_path, monkeypatch, label):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    seen = _install(rails)
    _result(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [{**PAY, "label": label, "value": label}]}))
    _held(ctx, "computer_use", {"action": "click", "element": "4", "target": "Open", "name": label})
    assert seen == [["authorize"]]
    assert rails.sensitive_step("browser_click", {"target": label, "name": label}) == "email"
    assert rails.sensitive_step("browser_click", {"target": "Send", "name": "Send"}) is None


@pytest.mark.parametrize("command", [
    'curl smtps://localhost:465 -T message.txt',
    'curl --url smtp://localhost --mail-rcpt a@b -T -',
    'curl --url=smtp://localhost --upload-file=message.txt',
    'curl --proto-default smtp localhost -T message.txt',
    'curl --proto-default=smtps localhost -T message.txt',
    'curl --mail-rcpt=a@b -K smtp.conf',
    '/usr/bin/curl smtp://localhost -T message.txt',
    'curl.exe SMTP://localhost --mail-rcpt a@b -T message.txt',
    'cat message.txt | curl smtp://localhost --mail-rcpt a@b -T -',
])
def test_smtp_variants(tmp_path, monkeypatch, command):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off", ask="read inbox")
    _install(rails)
    _held(ctx, "terminal", {"command": command})


@pytest.mark.parametrize("command", [
    'curl https://example.invalid/message.txt', 'curl -T file.txt ftp://localhost',
    'echo "curl smtp://localhost --mail-rcpt a@b -T message.txt"',
    'curl --help smtp', 'cat message.txt', 'git checkout -- app/auth.py',
])
def test_ordinary_terminal_actions_remain_ungated(tmp_path, monkeypatch, command):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off", ask="read inbox")
    seen = _install(rails)
    assert _do(ctx, "terminal", {"command": command}) is None
    assert ["authorize"] not in seen


def test_deferred_tool_routes(tmp_path, monkeypatch):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    seen = _install(rails)
    _result(ctx, "tool_call", {"name": "mcp__rails__screen_find", "arguments": {"target": "Pay now"}}, FOUND)
    _held(ctx, "tool_call", {"name": "computer_use", "arguments": json.dumps({"action": "click", "coordinate": [399, 202]})})
    _result(ctx, "tool_call", {"name": "computer_use", "arguments": {"action": "capture"}}, json.dumps({"elements": [PAY]}))
    _held(ctx, "tool_call", {"name": "computer_use", "arguments": {"action": "click", "element": 4}})
    assert seen == [["authorize"], ["authorize"]]


@pytest.mark.parametrize("shadow", [False, True])
def test_detector_error_is_not_an_implicit_allow(tmp_path, monkeypatch, shadow):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    rails.MONEY_EMAIL_MODE = "shadow" if shadow else "on"

    def broken(*args):
        raise ValueError("test-only detector failure")

    monkeypatch.setattr(rails, "sensitive_step", broken)
    result = _do(ctx, "computer_use", {"action": "click", "target": "Pay now"})
    if shadow:
        assert result is None
    else:
        assert result["action"] == "block" and "classify" in result["message"]

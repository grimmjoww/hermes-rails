"""PR #5 hook-level regressions. No tools execute and no network is used."""
import json

import pytest

from test_eyes import FOUND, _cua_setup, _do
from test_money_email import _install


PAY = {"index": 4, "role": "AXButton", "label": "Pay now", "bounds": [380, 180, 80, 40]}


def _result(ctx, tool, args, result):
    ctx.hooks["transform_tool_result"](tool_name=tool, args=args, result=result,
                                       status="success", session_id="s", turn_id="t1")


def _held(ctx, tool, args):
    result = ctx.hooks["pre_tool_call"](tool_name=tool, args=args, session_id="s", turn_id="t1")
    assert result and result["action"] == "block" and "check]" in result["message"]


def test_report_coordinate_find(tmp_path, monkeypatch):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch)
    seen = _install(rails)
    _do(ctx, "screen_find", {"target": "Pay now"}, FOUND)
    _held(ctx, "computer_use", {"action": "click", "coordinate": [399, 202]})
    assert seen == [["authorize"]]


@pytest.mark.parametrize("mode,ask", [("off", "use computer use"), ("on", "read the homepage")])
def test_report_element_without_cua_gate(tmp_path, monkeypatch, mode, ask):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode=mode, ask=ask)
    seen = _install(rails)
    _do(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [PAY]}))
    _held(ctx, "computer_use", {"action": "click", "element": 4})
    assert seen == [["authorize"]]


def test_report_curl_smtp(tmp_path, monkeypatch):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off", ask="read my inbox")
    seen = _install(rails)
    _held(ctx, "terminal", {"command": "curl smtp://localhost:25 --mail-from sender@example.com "
                             "--mail-rcpt recipient@example.com --upload-file message.txt"})
    assert ["authorize"] in seen


@pytest.mark.parametrize("label", ["Reply", "Forward"])
def test_report_duplicated_email_label(tmp_path, monkeypatch, label):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, vision=False)
    seen = _install(rails)
    _do(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [{**PAY, "label": label}]}))
    _held(ctx, "computer_use", {"action": "click", "element": 4, "target": label})
    assert seen == [["authorize"]]

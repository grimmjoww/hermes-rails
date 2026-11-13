"""Continued shell commands must reach the money/email gate; no tools execute."""
import pytest

from test_eyes import _cua_setup
from test_money_email import _install
from test_rails import load


CONTINUATIONS = ["\\\n", "`\n", "`\r\n"]


@pytest.mark.parametrize("continuation", CONTINUATIONS)
def test_report_continued_curl_is_held(tmp_path, monkeypatch, continuation):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off", ask="read my inbox")
    seen = _install(rails)
    command = "curl " + continuation + "  --url smtps://example.invalid:465 -T message.txt"
    result = ctx.hooks["pre_tool_call"](tool_name="terminal", args={"command": command},
                                       session_id="s", turn_id="t1")
    assert [call for call in seen if "authorize" in call] == [["authorize"]]
    assert result and result["action"] == "block" and "email check" in result["message"]


@pytest.mark.parametrize("continuation", CONTINUATIONS)
@pytest.mark.parametrize("template", [
    "curl {c} --url smtps://example.invalid:465 -T message.txt",
    "cu{c}rl --url smtp://example.invalid:25 -T -",
    "curl --url sm{c}tps://example.invalid:465 -T -",
    "curl --mail-{c}rcpt=a@example.invalid -T - example.invalid",
    "curl --proto-default {c} smtps example.invalid -T -",
    "echo ready && curl {c} --url=smtp://example.invalid -T -",
    "curl {c} --url smtp://example.invalid {c} --mail-rcpt a@example.invalid -T -",
    "send{c}mail a@example.invalid",
    "stripe {c} charges create",
    "curl -X POST https://api.str{c}ipe.com/v1/charges",
])
def test_continued_action_detection(tmp_path, continuation, template):
    rails = load(tmp_path)
    area = "money" if "stripe" in template or "str{c}ipe" in template else "email"
    assert rails._terminal_area(template.format(c=continuation)) == area


@pytest.mark.parametrize("continuation", CONTINUATIONS)
@pytest.mark.parametrize("verdict", ["no", "unsure", "down", "yes", "shadow"])
def test_continued_authorization_outcomes(tmp_path, monkeypatch, continuation, verdict):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    seen = _install(rails, p={"no": 0.1, "unsure": 0.5, "yes": 0.91}.get(verdict, 0.1),
                    boom=verdict == "down")
    if verdict == "shadow":
        rails.MONEY_EMAIL_MODE = "shadow"
    result = ctx.hooks["pre_tool_call"](
        tool_name="terminal", args={"cmd": "curl " + continuation + " --url smtp://example.invalid -T -"},
        session_id="s", turn_id="t1")
    assert [call for call in seen if "authorize" in call] == [["authorize"]]
    if verdict in {"yes", "shadow"}:
        assert result is None
    else:
        assert result and result["action"] == "block"


@pytest.mark.parametrize("continuation", CONTINUATIONS)
@pytest.mark.parametrize("template", [
    "curl {c} https://example.invalid/readme",
    "printf '%s' {c} smtp://example.invalid",
    "git {c} status",
])
def test_benign_continued_commands_do_not_ask_for_authorization(tmp_path, monkeypatch, continuation, template):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    seen = _install(rails)
    command = template.format(c=continuation)
    shell = "bash" if continuation.startswith(chr(92)) else "powershell"
    assert rails._terminal_area(command, shell) is None
    ctx.hooks["pre_tool_call"](tool_name=shell, args={"command": command},
                               session_id="s", turn_id="t1")
    assert ["authorize"] not in seen


def test_actual_newlines_remain_command_boundaries(tmp_path):
    rails = load(tmp_path)
    assert rails._terminal_area("echo ready\ncurl --url smtp://example.invalid -T -") == "email"
    assert rails._terminal_area("curl https://example.invalid\necho smtp://example.invalid") is None
    # A printed backslash-n is not a shell line continuation.
    assert rails._terminal_area(r"echo curl\n --url smtp://example.invalid") is None


@pytest.mark.parametrize("marker", [chr(92), chr(96)])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_escaped_escape_keeps_new_command_boundary(tmp_path, marker, newline):
    rails = load(tmp_path)
    command = "echo " + marker * 2 + newline + "curl --url smtp://example.invalid -T -"
    assert rails._terminal_area(command) == "email"
    assert not rails.read_only(command)
    # Odd runs still continue only for supported marker/newline pairs.
    if marker == chr(92) and newline == "\r\n":
        return
    command = "curl " + marker * 3 + newline + " --url smtp://example.invalid -T -"
    assert rails._terminal_area(command) == "email"

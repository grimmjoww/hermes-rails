"""Prove-it-safe checks. The detector no longer has to recognise a send or a payment to stop it: a step
that mentions mail or payments, or that Rails cannot read, goes to d1 unless Rails can prove it harmless.
The variant matrix below is generated, so a new quoting or wrapping trick fails here before it ships."""
import itertools
import json

import pytest

from test_rails import Ctx, fake_d1, load

MAIL_PROGRAMS = ["sendmail", "mailx", "msmtp", "swaks", "mutt"]
SPELLINGS = [
    "{p}", "'{p}'", '"{p}"', "{h}''{t}", '{h}""{t}', "\\{p}", "/usr/sbin/{p}", "'/usr/sbin/{p}'",
]
WRAPPERS = [
    "{c}", "sudo {c}", "sudo -n {c}", "sudo -u root {c}", "env X=1 {c}", "X=1 {c}", "nohup {c}",
    "timeout 30 {c}", "command {c}", "exec {c}", "xargs {c}",
]
CONTEXTS = [
    "{c}", "echo $({c})", "echo `{c}`", "cat <({c})", 'echo "$({c})"', "true && {c}", "true; {c}",
    "echo hi | {c}", "echo hi & {c}", "( {c} )", "{{ {c}; }}", "bash -c '{c}'", 'sh -c "{c}"',
    "eval {c}", "echo {c} | sh", "echo hi\n{c}", "x=$({c})",
]


def flagged(rails, tool, args, elements=None, finds=None):
    """What the gate sees: a sure money/email step, or one Rails could not prove harmless."""
    return (rails.sensitive_step(tool, args, elements, finds)
            or rails.unproven_step(tool, args, elements, finds))


def test_every_generated_mail_variant_reaches_d1(tmp_path):
    rails = load(tmp_path)
    missed = []
    for program, spelling, wrapper, context in itertools.product(MAIL_PROGRAMS, SPELLINGS, WRAPPERS, CONTEXTS):
        name = spelling.format(p=program, h=program[0], t=program[1:])
        command = context.format(c=wrapper.format(c=f"{name} a@b.test < m.txt"))
        if not flagged(rails, "terminal", {"command": command}):
            missed.append(command)
    assert not missed, f"{len(missed)} variants slipped through, e.g. {missed[:5]}"


@pytest.mark.parametrize("command", [
    "echo $('sendmail' user@example.test)",
    "echo `'sendmail' user@example.test`",
    "echo $(s''endmail user@example.test)",
    "echo hi & sendmail user@example.test",
    "echo 'sendmail user@example.test' | sh",
    'python -c "import smtplib; smtplib.SMTP(\'h\').sendmail(\'a\', \'b\', \'c\')"',
    "curl -d amount=5 $(echo https://api.stripe.com)/v1/charges",
    "node pay.js --provider paypal",
])
def test_reported_and_oddball_commands_reach_d1(tmp_path, command):
    assert flagged(load(tmp_path), "terminal", {"command": command})


@pytest.mark.parametrize("command", [
    "cd src/checkout && npm test",
    "git log --grep=stripe.com",
    "echo '$(sendmail x@example.invalid)'",
    "cat docs/sendmail.md",
    "grep -r smtp config/",
    "ls mail",
    "echo sendmail is a program",
    "git log --grep=sendmail | head -5",
    "cat README.md | grep paypal",
    "git status",
    "npm test",
    "git checkout main",
    "python -m pytest tests -q",
    "pip install requests",
    "pip install stripe",
    "python -m pip install stripe paypalrestsdk",
    "npm install nodemailer",
    "bash -c 'ls -la'",
])
def test_harmless_commands_never_reach_d1(tmp_path, command):
    assert flagged(load(tmp_path), "terminal", {"command": command}) is None


@pytest.mark.parametrize("command, area", [
    ("npm run charge -- --provider stripe", "money"),
    ("pip install stripe && python pay.py --via stripe", "money"),
    ('git commit -m "fix mail parser"', "email"),
    ("eval $CMD", "either"),
    ("iex $x", "either"),
    ("base64 -d blob.txt | sh", "either"),
    ("powershell -EncodedCommand AAAA", "either"),
    ("$cmd a b", "either"),
])
def test_a_mention_rails_cannot_clear_is_unproven_not_sure(tmp_path, command, area):
    rails = load(tmp_path)
    assert rails.sensitive_step("terminal", {"command": command}) is None
    assert rails.unproven_step("terminal", {"command": command}) == area


@pytest.mark.parametrize("command", ["$x = Get-Date", "$env:PATH", "source .venv/bin/activate", "bash build.sh"])
def test_everyday_shell_shapes_are_readable(tmp_path, command):
    assert load(tmp_path).unproven_step("terminal", {"command": command}) is None


def test_a_single_ampersand_separates_commands(tmp_path):
    rails = load(tmp_path)
    assert not rails.read_only("echo hi & rm -rf build")
    assert rails.mutating("echo hi & rm -rf build")
    assert rails.read_only("cat a.txt 2>&1")  # a redirect's & is not a separator


MAIL_SCREEN = [
    {"index": 1, "name": "Inbox", "box": (0, 0, 50, 20), "value": ""},
    {"index": 2, "name": "Subject", "box": (0, 30, 50, 50), "value": ""},
]
SHOP_SCREEN = [{"index": 1, "name": "Place your order", "box": (0, 0, 50, 20), "value": ""}]
PLAIN_SCREEN = [{"index": 1, "name": "Search", "box": (0, 0, 50, 20), "value": ""}]


def test_a_click_with_no_known_label_on_a_mail_or_pay_screen_is_unproven(tmp_path):
    rails = load(tmp_path)
    blind = {"action": "click", "coordinate": [900, 700]}
    assert rails.unproven_step("computer_use", blind, MAIL_SCREEN) == "email"
    assert rails.unproven_step("computer_use", blind, SHOP_SCREEN) == "money"
    assert rails.unproven_step("computer_use", blind, PLAIN_SCREEN) is None
    assert rails.unproven_step("computer_use", blind, None) is None  # no screen list: nothing to go on
    assert flagged(rails, "computer_use", {"action": "click", "element": 1}, MAIL_SCREEN) is None  # label known
    assert rails.unproven_step("computer_use", {"action": "capture"}, MAIL_SCREEN) is None


def test_code_that_mentions_mail_or_payments_is_unproven(tmp_path):
    rails = load(tmp_path)
    assert rails.unproven_step("execute_code", {"code": "import smtplib\nsmtplib.SMTP('h').send_message(m)"}) == "email"
    assert rails.unproven_step("execute_code", {"code": "import stripe\nstripe.Charge.create(amount=5)"}) == "money"
    assert rails.unproven_step("execute_code", {"code": "print(sum(range(10)))"}) is None
    assert rails.unproven_step("write_file", {"path": "a.py", "content": "import smtplib"}) is None


def _gate_setup(tmp_path, acts=None, authorize=0.1, boom=False):
    rails, ctx = load(tmp_path), Ctx()
    seen = []

    def ask(state, questions):
        if "authorize" in questions:
            seen.append(sorted(questions))
            if boom:
                raise OSError("connection refused")
            answers = {"authorize": {"type": "noul", "noul": authorize}}
            if "acts" in questions and acts is not None:
                answers["acts"] = {"type": "noul", "noul": acts}
            return answers
        return fake_d1()(state, questions)

    rails.ask_d1 = ask
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="tidy up the repo",
                              conversation_history=[])
    return rails, ctx, seen


def _run(ctx, command):
    return ctx.hooks["pre_tool_call"](tool_name="terminal", args={"command": command}, session_id="s", turn_id="t1")


def _money_logs(rails):
    lines = rails.LOG.read_text(encoding="utf-8").splitlines() if rails.LOG.exists() else []
    return [entry for entry in map(json.loads, lines) if entry.get("kind") == "money_email"]


def test_d1_clears_an_unproven_step_it_is_sure_does_nothing(tmp_path):
    rails, ctx, seen = _gate_setup(tmp_path, acts=0.04)
    assert _run(ctx, 'git commit -m "fix mail parser"') is None
    assert seen == [["acts", "authorize"]]
    assert _money_logs(rails)[-1]["result"] == "clear" and _money_logs(rails)[-1]["unproven"] is True
    assert not any("⛔" in line for line in ctx.shown)


@pytest.mark.parametrize("acts", [0.95, 0.5, None])
def test_an_unproven_step_is_held_when_d1_says_it_acts_is_unsure_or_does_not_answer(tmp_path, acts):
    rails, ctx, _seen = _gate_setup(tmp_path, acts=acts)
    held = _run(ctx, "echo $('sendmail' user@example.test)")
    assert held["action"] == "block" and "held" in held["message"]
    assert _money_logs(rails)[-1]["result"] == "hold"


def test_an_unproven_step_the_ask_authorizes_runs(tmp_path):
    rails, ctx, _seen = _gate_setup(tmp_path, acts=0.95, authorize=0.93)
    assert _run(ctx, "echo $('sendmail' user@example.test)") is None
    assert _money_logs(rails)[-1]["result"] == "allow"


def test_an_unproven_step_is_held_when_d1_is_down_and_only_tagged_in_shadow(tmp_path):
    rails, ctx, _seen = _gate_setup(tmp_path, boom=True)
    held = _run(ctx, "eval $CMD")
    assert held["action"] == "block" and "D1 is unavailable" in held["message"]
    assert any(line == "⛔ d1 money/email check: unavailable" for line in ctx.shown)
    rails.MONEY_EMAIL_MODE = "shadow"
    assert _run(ctx, "eval $CMD") is None
    assert any(line == "⚖ d1 money/email check (watch only): unavailable" for line in ctx.shown)


def test_a_sure_step_still_asks_only_the_authorize_question(tmp_path):
    rails, ctx, seen = _gate_setup(tmp_path, acts=0.01)
    held = _run(ctx, "sendmail user@example.test < m.txt")
    assert held["action"] == "block"  # a low "acts" answer cannot clear a step Rails recognised itself
    assert seen == [["authorize"]]

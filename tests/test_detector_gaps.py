"""Gaps found in review of 0.4.3: buy-button wording, a lone Send in a mail app, wrapped or prefixed
commands, false alarms, the plugin.yaml 'on' choice, log masking, and session eviction."""
import json
from pathlib import Path

import pytest

from test_rails import Ctx, load

HERE = Path(__file__).resolve().parents[1]
MAIL_SCREEN = [
    {"index": 1, "name": "Inbox", "box": None, "value": ""},
    {"index": 2, "name": "Subject", "box": None, "value": ""},
    {"index": 3, "name": "Send ‪(Ctrl-Enter)‬", "box": None, "value": ""},
    {"index": 4, "name": "Delete", "box": None, "value": ""},
    {"index": 5, "name": "Archive", "box": None, "value": ""},
]
CHAT_SCREEN = [
    {"index": 1, "name": "Message #general", "box": None, "value": ""},
    {"index": 3, "name": "Send", "box": None, "value": ""},
    {"index": 4, "name": "Delete", "box": None, "value": ""},
]


def _click(rails, label):
    return rails.sensitive_step("computer_use", {"action": "click", "element": 3},
                                [{"index": 3, "name": label, "box": None, "value": ""}])


@pytest.mark.parametrize("label", [
    "Place your order", "Place order", "Purchase", "Complete purchase", "Order now", "Submit order",
    "Confirm order", "Buy now", "Confirm and pay", "Checkout", "Proceed to check out", "Check out",
])
def test_buy_buttons_are_money(tmp_path, label):
    assert _click(load(tmp_path), label) == "money"


@pytest.mark.parametrize("label", [
    "Check out this video", "Check out the latest deals", "Pay attention", "Add to cart", "Order history",
])
def test_everyday_labels_are_not_money(tmp_path, label):
    assert _click(load(tmp_path), label) is None


@pytest.mark.parametrize("index", [3, 4, 5])
def test_a_lone_send_delete_or_archive_in_a_mail_app_is_email(tmp_path, index):
    rails = load(tmp_path)
    assert rails.sensitive_step("computer_use", {"action": "click", "element": index}, MAIL_SCREEN) == "email"


@pytest.mark.parametrize("index", [3, 4])
def test_a_lone_send_or_delete_outside_a_mail_app_is_not_email(tmp_path, index):
    rails = load(tmp_path)
    assert rails.sensitive_step("computer_use", {"action": "click", "element": index}, CHAT_SCREEN) is None


def test_the_send_shortcut_in_a_mail_app_is_email(tmp_path):
    rails = load(tmp_path)
    assert rails.sensitive_step("computer_use", {"action": "key", "keys": "ctrl+Return"}, MAIL_SCREEN) == "email"
    assert rails.sensitive_step("computer_use", {"action": "key", "keys": "cmd+enter"}, MAIL_SCREEN) == "email"
    assert rails.sensitive_step("computer_use", {"action": "key", "keys": "ctrl+Return"}, CHAT_SCREEN) is None
    assert rails.sensitive_step("computer_use", {"action": "key", "keys": "ctrl+c"}, MAIL_SCREEN) is None


@pytest.mark.parametrize("command, area", [
    ("echo $(curl -X POST https://api.stripe.com/v1/charges -d amount=500)", "money"),
    ("cat <(curl -d amount=5 https://api.stripe.com/v1/payments)", "money"),
    ("echo `curl -X POST https://api.paypal.com/v2/checkout/orders`", "money"),
    ("echo $(sendmail a@b.c < m.txt)", "email"),
    ("sudo sendmail a@b.c < m.txt", "email"),
    ("sudo -u root sendmail a@b.c < m.txt", "email"),
    ("echo hi | sudo msmtp a@b.c", "email"),
    ("env X=1 swaks --to a@b.c", "email"),
    ("FOO=bar nohup msmtp a@b.c", "email"),
    ("timeout 30 mutt -s hi a@b.c", "email"),
])
def test_wrapped_and_prefixed_commands_are_detected(tmp_path, command, area):
    assert load(tmp_path).sensitive_step("terminal", {"command": command}) == area


@pytest.mark.parametrize("command", [
    "cd src/checkout && npm test", "ls /srv/checkout", "git checkout main", "cat docs/v1/payments.md",
    "echo $(date)", "sudo ls -la", "env",
])
def test_ordinary_commands_are_not_money_or_email(tmp_path, command):
    assert load(tmp_path).sensitive_step("terminal", {"command": command}) is None


@pytest.mark.parametrize("command", [
    "cat $(rm -rf build)", "echo `git push -f`", "ls; cat <(rm x)", "echo $(date)",
])
def test_a_command_with_a_substitution_is_never_read_only(tmp_path, command):
    assert load(tmp_path).read_only(command) is False


@pytest.mark.parametrize("command", [
    "cat $(rm -rf build)", "echo `git push -f`", "ls; cat <(rm x)", "sudo rm -rf build", "xargs rm",
    "env X=1 git push",
])
def test_a_wrapped_mutation_is_still_mutating(tmp_path, command):
    assert load(tmp_path).mutating(command) is True


def test_plain_reads_stay_read_only(tmp_path):
    rails = load(tmp_path)
    for command in ("cat README.md", "ls -la && git status", "grep -rn foo . | head", "sudo cat /etc/hosts"):
        assert rails.read_only(command) is True, command
    assert rails.mutating("cat README.md") is False


def test_every_plugin_yaml_choice_and_str_default_is_a_string():
    yaml = pytest.importorskip("yaml")
    schema = yaml.safe_load((HERE / "plugin.yaml").read_text(encoding="utf-8"))["config_schema"]
    for key, spec in schema.items():
        for choice in spec.get("choices", []):
            assert isinstance(choice, str), f"{key}: choice {choice!r} is not a string (quote it)"
        if spec.get("type") == "str":
            assert isinstance(spec.get("default"), str), f"{key}: default {spec.get('default')!r} is not a string"
    assert schema["money_email_check"]["choices"] == ["on", "shadow"]
    assert schema["money_email_check"]["default"] == "on"


def test_a_boolean_true_setting_still_means_on(tmp_path):
    """An older install, or a YAML reader, can hand back True for a bare `on`."""
    rails = load(tmp_path)

    class Settings(Ctx):
        def get_config(self, key, default=None):
            return True if key == "money_email_check" else default

    rails.MONEY_EMAIL_MODE = "shadow"
    rails.apply_settings(Settings())
    assert rails.MONEY_EMAIL_MODE == "on"


def test_long_tokens_are_masked_in_every_logged_string(tmp_path):
    rails = load(tmp_path)
    secret = "sk_live_" + "A1b2C3d4" * 5
    rails._log({"session": "s-1", "turn": "t-1", "kind": "x", "error": f"OSError: bad key {secret}",
                "side": [f"terminal `curl -H {secret}`"]})
    line = rails.LOG.read_text(encoding="utf-8")
    assert secret not in line
    assert json.loads(line)["session"] == "s-1"


def test_a_known_session_does_not_evict_another(tmp_path):
    rails = load(tmp_path)
    ctx = Ctx()
    rails.register(ctx)
    start = ctx.hooks["pre_llm_call"]
    for i in range(rails._MAX):
        start(session_id=f"s{i}", turn_id="1", user_message="hello there", conversation_history=[])
    start(session_id="s5", turn_id="2", user_message="hello again", conversation_history=[])
    assert "s0" in rails._ASKS and len(rails._ASKS) == rails._MAX


# --- review follow-ups on PR #11 ---------------------------------------------

@pytest.mark.parametrize("command", [
    "env -i rm cat", "sudo -n rm cat", "sudo -u rm cat build", "sudo -u root rm -rf build", "timeout -k 5 30 rm x",
    "nice -n 5 git push", "xargs -n 1 rm",
])
def test_wrapper_flags_cannot_hide_a_mutation(tmp_path, command):
    rails = load(tmp_path)
    assert rails.mutating(command) is True, command
    assert rails.read_only(command) is False, command


@pytest.mark.parametrize("command", ["sudo -n sendmail a@b.c", "env -i msmtp a@b.c", "sudo -u mail sendmail a@b.c"])
def test_wrapper_flags_cannot_hide_a_mail_program(tmp_path, command):
    assert load(tmp_path).sensitive_step("terminal", {"command": command}) == "email"


@pytest.mark.parametrize("command", [
    "echo '$(sendmail user@example.com)'", "echo '$(rm -rf build)'", "echo '`git push -f`'",
    "grep -n '$(' README.md",
])
def test_a_single_quoted_substitution_is_only_text(tmp_path, command):
    rails = load(tmp_path)
    assert rails.sensitive_step("terminal", {"command": command}) is None
    assert rails.mutating(command) is False
    assert rails.read_only(command) is True


@pytest.mark.parametrize("command", [
    'echo "it\'s $(rm -rf build)"', 'echo "$(rm -rf build)"', "echo 'a' $(rm -rf build)", "echo it's $(rm -rf build)",
])
def test_a_live_substitution_near_quotes_still_counts(tmp_path, command):
    rails = load(tmp_path)
    assert rails.mutating(command) is True, command
    assert rails.read_only(command) is False, command


@pytest.mark.parametrize("command", [
    "git log --grep=stripe.com", "git log -S paypal.com | head", "grep -rn stripe.com src", "cat notes/stripe.com.md",
])
def test_reading_about_payments_is_not_a_payment(tmp_path, command):
    assert load(tmp_path).sensitive_step("terminal", {"command": command}) is None


@pytest.mark.parametrize("command", [
    "curl -X POST shop.example/checkout -d amount=500", "curl -d amount=5 localhost:3000/checkout",
    "wget --post-data amount=5 shop.example/v1/payments", "sudo -n curl -X POST shop.example/checkout",
    "curl -X POST https://shop.example/checkout",
])
def test_a_schemeless_payment_url_is_still_money(tmp_path, command):
    assert load(tmp_path).sensitive_step("terminal", {"command": command}) == "money"

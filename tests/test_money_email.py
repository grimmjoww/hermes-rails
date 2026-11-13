"""Money and email steps ask d1 even on a computer-use turn. d1 down holds. shadow only tags."""
import json

from test_eyes import FOUND, _cua_setup, _do
from test_rails import Ctx, fake_d1, load


def _logs(rails):
    if not rails.LOG.exists():
        return []
    return [json.loads(line) for line in rails.LOG.read_text(encoding="utf-8").splitlines()]


def _install(rails, p=0.1, boom=False):
    seen = []

    def ask(state, questions):
        seen.append(list(questions))
        if "authorize" in questions:
            if boom:
                raise OSError("connection refused")
            return {"authorize": {"type": "noul", "noul": p}}
        return fake_d1()(state, questions)

    rails.ask_d1 = ask
    return seen


def test_a_purchase_click_is_held_without_authorization_and_allowed_with_it(tmp_path, monkeypatch):
    rails, ctx, _steer = _cua_setup(tmp_path, monkeypatch, vision=False)
    assert rails.CUA_DRIVER == "vision"
    seen = _install(rails, 0.12)
    held = _do(ctx, "computer_use", {"action": "click", "target": "Checkout"})
    assert held["action"] == "block"
    assert "Confirm with the user" in held["message"]
    assert "screen_find" not in held["message"]
    assert any(line == "⛔ d1 money check: not authorized" for line in ctx.shown)
    assert seen == [["authorize"]]
    assert any(entry.get("kind") == "money_email" and entry.get("area") == "money" and entry.get("result") == "hold"
               for entry in _logs(rails))

    _install(rails, 0.50)
    unsure = _do(ctx, "computer_use", {"action": "click", "target": "Pay now"})
    assert unsure["action"] == "block" and "Confirm with the user" in unsure["message"]

    _install(rails, 0.91)
    assert _do(ctx, "computer_use", {"action": "click", "target": "Checkout"}) is None
    assert any(line == "✅ d1 money check 0.91" for line in ctx.shown)
    _install(rails, 0.70)
    assert _do(ctx, "computer_use", {"action": "click", "target": "Place order"}) is None
    assert any(line == "✅ d1 money check 0.70" for line in ctx.shown)


def test_an_email_send_is_held_or_allowed(tmp_path):
    rails, ctx = load(tmp_path), Ctx()
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="read my inbox")
    _install(rails, 0.2)
    held = ctx.hooks["pre_tool_call"](tool_name="gmail_send", args={"action": "send", "to": "a@b.c"},
                                      session_id="s", turn_id="t1")
    assert held["action"] == "block" and "Confirm with the user" in held["message"]
    assert any(line == "⛔ d1 email check: not authorized" for line in ctx.shown)

    _install(rails, 0.91)
    assert ctx.hooks["pre_tool_call"](tool_name="send_email", args={"action": "send"},
                                      session_id="s", turn_id="t1") is None
    assert any(line == "✅ d1 email check 0.91" for line in ctx.shown)


def test_d1_down_holds_the_step(tmp_path, monkeypatch):
    rails, ctx, _steer = _cua_setup(tmp_path, monkeypatch, vision=False)
    _install(rails, boom=True)
    held = _do(ctx, "computer_use", {"action": "click", "target": "Subscribe"})
    assert held["action"] == "block"
    assert "D1 is unavailable" in held["message"]
    assert any(line == "⛔ d1 money check: unavailable" for line in ctx.shown)
    assert any(entry.get("kind") == "money_email" and entry.get("result") == "down" for entry in _logs(rails))


def test_shadow_only_tags(tmp_path, monkeypatch):
    rails, ctx, _steer = _cua_setup(tmp_path, monkeypatch, vision=False)
    rails.MONEY_EMAIL_MODE = "shadow"
    _install(rails, 0.1)
    assert _do(ctx, "computer_use", {"action": "click", "target": "Checkout"}) is None
    assert any(line == "⚖ d1 money check (watch only): not authorized" for line in ctx.shown)
    assert not any(line.startswith("⛔ d1 money") for line in ctx.shown)
    assert any(entry.get("kind") == "money_email" and entry.get("mode") == "shadow" and entry.get("result") == "hold"
               for entry in _logs(rails))


def test_the_check_runs_on_a_computer_use_turn_before_the_early_return(tmp_path, monkeypatch):
    """Typing is not held by the computer-use gate, and a decided click used to skip judge()."""
    rails, ctx, _steer = _cua_setup(tmp_path, monkeypatch, vision=True)
    rails.CUA_DRIVER = "text_first"  # the check applies under every driver
    seen = _install(rails, 0.12)
    typed = _do(ctx, "computer_use", {"action": "type", "text": "card number 4242"})
    assert typed["action"] == "block" and "money" in typed["message"] and "screen_find" not in typed["message"]
    assert seen == [["authorize"]]

    assert _do(ctx, "screen_find", {"target": "Checkout"}, FOUND) is None
    assert seen == [["authorize"]]  # looking at the screen is not the purchase
    clicked = _do(ctx, "computer_use", {"action": "click", "target": "Checkout"})
    assert clicked["action"] == "block" and "Confirm with the user" in clicked["message"]
    assert seen == [["authorize"], ["authorize"]]

    _install(rails, 0.91)
    assert _do(ctx, "computer_use", {"action": "click", "target": "Search"}) is None
    assert _do(ctx, "computer_use", {"action": "type", "text": "hermes"}) is None


def test_ordinary_reads_and_navigation_are_not_money_or_email(tmp_path, monkeypatch):
    rails, ctx, _steer = _cua_setup(tmp_path, monkeypatch, vision=False)
    seen = _install(rails, 0.1)
    pay = {"index": 4, "role": "AXButton", "label": "Pay now", "bounds": [10, 10, 80, 30]}
    cart = {"index": 6, "role": "AXButton", "label": "Add to cart", "bounds": [412, 300, 108, 36]}
    assert _do(ctx, "computer_use", {"action": "capture"}, json.dumps({"elements": [pay, cart]})) is None

    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Add to cart"}) is None
    assert rails.sensitive_step("computer_use", {"action": "click", "element": 6},
                                rails._CUA["s"]["elements"]) is None
    assert rails.sensitive_step("read_file", {"path": "app/payments.py"}) is None
    assert rails.sensitive_step("browser_navigate", {"url": "https://www.amazon.com/"}) is None
    assert rails.sensitive_step("browser_navigate", {"url": "https://www.amazon.com/checkout"}) == "money"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Search"}) is None
    assert rails.sensitive_step("computer_use", {"action": "type", "text": "hello"}) is None
    assert rails.sensitive_step("terminal", {"command": "git checkout -- app/auth.py"}) is None
    assert rails.sensitive_step("terminal", {"command": "sudo git checkout -- app/auth.py"}) is None
    assert rails.sensitive_step("terminal", {"command": "curl -X POST x"}) is None
    assert rails.sensitive_step("terminal", {"command": "cat app/payments.py"}) is None
    assert rails.sensitive_step("terminal", {"command": "curl https://api.stripe.com/v1/payment_intents"}) == "money"
    assert rails.sensitive_step("terminal", {"command": "sudo stripe charges create --amount 5000"}) == "money"
    assert rails.sensitive_step("terminal", {"command": "env STRIPE_KEY=sk stripe charges create"}) == "money"
    assert rails.sensitive_step("terminal", {"command": 'bash -c "stripe charges create"'}) == "money"
    assert rails.sensitive_step("terminal", {"command": "echo stripe charges create"}) is None
    assert rails.sensitive_step("terminal", {"command": "git send-email origin"}) == "email"
    assert rails.sensitive_step("terminal", {"command": "sudo git send-email origin"}) == "email"
    assert rails.sensitive_step("terminal", {"command": "sudo sendmail alice@example.com"}) == "email"
    assert rails.sensitive_step("terminal", {"command": "mail alice@example.com"}) == "email"
    assert rails.sensitive_step("terminal", {"command": "mail"}) is None
    assert rails.sensitive_step("terminal", {"command": "mail -f /var/mail/user"}) is None
    assert rails.sensitive_step("terminal", {"command": "powershell -Command Send-MailMessage -To a@b.c"}) == "email"
    assert rails.sensitive_step("gmail_list", {"action": "list"}) is None
    assert rails.sensitive_step("outlook", {"action": "reply"}) == "email"
    assert rails.sensitive_step("delete_email", {}) == "email"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Reply"}) == "email"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "archive this mail"}) == "email"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Send"}) is None
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Donate"}) == "money"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Transfer money"}) == "money"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Place your order"}) == "money"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "Purchase"}) == "money"
    assert rails.sensitive_step("computer_use", {"action": "click", "target": "order"}) is None
    assert rails.sensitive_step("terminal", {"command": "echo https://api.stripe.com/v1/charges"}) is None
    assert rails.sensitive_step("screen_find", {"target": "Checkout"}) is None
    assert rails.sensitive_step("computer_use", {"action": "click", "element": 4},
                                rails._CUA["s"]["elements"]) == "money"

    assert _do(ctx, "computer_use", {"action": "click", "element": 6}) is None
    assert _do(ctx, "computer_use", {"action": "click", "coordinate": [450, 320]}) is None
    assert _do(ctx, "computer_use", {"action": "type", "text": "hello"}) is None
    assert _do(ctx, "screen_find", {"target": "Checkout"}) is None
    assert ctx.hooks["pre_tool_call"](tool_name="read_file", args={"path": "app/payments.py"},
                                      session_id="s", turn_id="t1") is None

    plain, ordinary = load(tmp_path), Ctx()
    plain.register(ordinary)
    ordinary.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="what is on the homepage")
    quiet = _install(plain, 0.1)
    for tool, args in (
            ("browser_navigate", {"url": "https://www.amazon.com/"}),
            ("terminal", {"command": "git checkout -- app/auth.py"}),
            ("terminal", {"command": "curl -X POST x"}),
            ("gmail_list", {"action": "list"}),
            ("read_file", {"path": "app/payments.py"})):
        result = ordinary.hooks["pre_tool_call"](tool_name=tool, args=args, session_id="s", turn_id="t1")
        assert not (result and "check] This" in result.get("message", ""))
    assert all("authorize" not in questions for questions in quiet)
    assert seen == []

    held = _do(ctx, "computer_use", {"action": "click", "element": 4})
    assert held["action"] == "block" and "Confirm with the user" in held["message"]
    point = _do(ctx, "computer_use", {"action": "click", "coordinate": [20, 20]})
    assert point["action"] == "block" and "money" in point["message"]
    assert all(questions == ["authorize"] for questions in seen)

    order = _do(ctx, "computer_use", {"action": "click", "target": "Place your order"})
    assert order["action"] == "block" and "money" in order["message"]
    wrapped = ctx.hooks["pre_tool_call"](tool_name="terminal",
                                         args={"command": "sudo stripe charges create --amount 5000"},
                                         session_id="s", turn_id="t1")
    assert wrapped["action"] == "block" and "money" in wrapped["message"]
    plain_mail = ctx.hooks["pre_tool_call"](tool_name="terminal",
                                            args={"command": "mail alice@example.com"},
                                            session_id="s", turn_id="t1")
    assert plain_mail["action"] == "block" and "email" in plain_mail["message"]

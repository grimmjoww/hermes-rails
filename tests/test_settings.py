"""Rails reads its modes from the Desktop settings form (plugins.entries.rails.settings)."""
from test_rails import Ctx, fake_d1, load


class SettingsCtx(Ctx):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


def test_settings_from_the_desktop_form_apply_on_the_next_message(tmp_path):
    rails, ctx = load(tmp_path), SettingsCtx({"workflow_mode": "off", "scope_mode": "block", "look_step_budget": 3})
    rails.ask_d1 = fake_d1(kind="bug")
    rails.register(ctx)
    assert ctx.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="fix the login bug") is None
    assert (rails.WORKFLOW_MODE, rails.MODE, rails.LOOK_STEP_BUDGET) == ("off", "block", 3)


def test_invalid_settings_keep_the_defaults(tmp_path):
    rails, ctx = load(tmp_path), SettingsCtx({"workflow_mode": "maybe", "scope_mode": 7, "look_step_budget": -1})
    rails.ask_d1 = fake_d1()
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="hi there")
    assert (rails.WORKFLOW_MODE, rails.MODE, rails.LOOK_STEP_BUDGET, rails.CUA_MODE) == ("on", "shadow", 8, "shadow")


def test_manifest_declares_the_form(tmp_path):
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    for key in ("workflow_mode", "scope_mode", "look_step_budget", "computer_use_mode", "user_label", "agent_label",
                "element_list_max", "computer_use_driver", "money_email_check"):
        assert f"  {key}:" in text
    assert "default: shadow" in text.split("computer_use_mode:", 1)[1].split("user_label:", 1)[0]


def test_element_list_max_applies_and_invalid_keeps_80(tmp_path):
    rails, ctx = load(tmp_path), SettingsCtx({"element_list_max": 12})
    rails.ask_d1 = fake_d1()
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="hi there")
    assert rails.ELEMENT_LIST_MAX == 12
    rails2, ctx2 = load(tmp_path), SettingsCtx({"element_list_max": 0})
    rails2.ask_d1 = fake_d1()
    rails2.register(ctx2)
    ctx2.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="hi there")
    assert rails2.ELEMENT_LIST_MAX == 80


def test_computer_use_driver_defaults_to_vision(tmp_path):
    from pathlib import Path
    rails = load(tmp_path)
    assert rails.CUA_DRIVER == "vision"
    text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("computer_use_driver:", 1)[1]
    assert "text_first" in block
    assert "default: vision" in block or 'default: "vision"' in block
    rails.ask_d1 = fake_d1()
    rails.register(SettingsCtx({"computer_use_driver": "text_first"}))
    rails.apply_settings(SettingsCtx({"computer_use_driver": "text_first"}))
    assert rails.CUA_DRIVER == "text_first"
    rails.apply_settings(SettingsCtx({"computer_use_driver": "maybe"}))
    assert rails.CUA_DRIVER == "text_first"  # an invalid value keeps the current setting
    fresh = load(tmp_path)
    fresh.ask_d1 = fake_d1()
    fresh.apply_settings(SettingsCtx({"computer_use_driver": "nope"}))
    assert fresh.CUA_DRIVER == "vision"


def test_money_email_check_defaults_to_on_and_has_no_off(tmp_path):
    from pathlib import Path
    rails = load(tmp_path)
    assert rails.MONEY_EMAIL_MODE == "on"
    text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("money_email_check:", 1)[1]
    choices = block.split("choices:", 1)[1].split("\n", 1)[0]
    assert "on" in choices and "shadow" in choices and "off" not in choices
    assert "default: on" in block
    rails.ask_d1 = fake_d1()
    rails.apply_settings(SettingsCtx({"money_email_check": "shadow"}))
    assert rails.MONEY_EMAIL_MODE == "shadow"
    rails.apply_settings(SettingsCtx({"money_email_check": "off"}))
    assert rails.MONEY_EMAIL_MODE == "shadow"  # off is not a choice, so the current setting stays
    fresh = load(tmp_path)
    fresh.ask_d1 = fake_d1()
    fresh.apply_settings(SettingsCtx({"money_email_check": "nope"}))
    assert fresh.MONEY_EMAIL_MODE == "on"


def test_user_and_agent_labels_can_be_set_in_config(tmp_path):
    rails, ctx = load(tmp_path), SettingsCtx({"user_label": "Willie", "agent_label": "Rei"})
    rails.ask_d1 = fake_d1()
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="hi there")
    assert (rails.USER, rails.AGENT) == ("Willie", "Rei")
    note = rails.harness_note("hello")
    assert note.startswith("[Rails, the harness, not a message from Willie]")

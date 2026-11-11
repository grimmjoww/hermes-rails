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
    assert (rails.WORKFLOW_MODE, rails.MODE, rails.LOOK_STEP_BUDGET) == ("on", "shadow", 8)


def test_manifest_declares_the_form(tmp_path):
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8")
    for key in ("workflow_mode", "scope_mode", "look_step_budget"):
        assert f"  {key}:" in text

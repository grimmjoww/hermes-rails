"""Rails scope gate, with a stand-in for d1 and for Hermes (nothing is launched). Run: python -m pytest -q tests"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def load(tmp_path):
    spec = importlib.util.spec_from_file_location("rails_under_test", HERE / "__init__.py")
    rails = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rails)
    rails.LOG = tmp_path / "rails.jsonl"
    return rails


class Handle:
    def __init__(self, ctx, text):
        self.ctx, self.ok, self.error = ctx, True, None
        ctx.shown.append(text)

    def edit(self, text):
        self.ctx.shown[-1] = text
        self.ctx.edits += 1
        return {"ok": True, "edited": True}


class Ctx:
    def __init__(self):
        self.hooks, self.shown, self.posts, self.edits = {}, [], 0, 0

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def post_message(self, text):
        self.posts += 1
        return Handle(self, text)


def _choice(choice, p, keys):
    return {"choice": choice, "probabilities": {choice: p, **{k: (1 - p) / 2 for k in keys if k != choice}}}


def fake_d1(scope="on_task", p=0.9, intent="change", p_intent=0.9, changes=0.1, seen=None, kind="other", p_kind=0.9):
    def ask(state, questions):
        if seen is not None:
            seen.append(state)
        if "kind" in questions:
            return {"kind": _choice(kind, p_kind, ("bug", "feature", "plan", "other"))}
        if "intent" in questions:
            return {"intent": _choice(intent, p_intent, ("look", "change", "unclear"))}
        return {"scope": _choice(scope, p, ("on_task", "side_quest", "unclear")), "changes": {"noul": changes}}
    return ask


@pytest.fixture
def setup(tmp_path):
    rails, ctx = load(tmp_path), Ctx()
    rails.ask_d1 = fake_d1()
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="fix the login test",
                              conversation_history=[{"role": "assistant", "content": "Want me to fix it?"}])
    return rails, ctx


def call(rails, ctx, tool="terminal", args=None):
    return ctx.hooks["pre_tool_call"](tool_name=tool, args=args or {"command": "pytest tests/test_login.py"},
                                      session_id="s", turn_id="t1")


def test_adds_nothing_to_the_prompt(tmp_path):
    rails, ctx = load(tmp_path), Ctx()
    rails.ask_d1 = fake_d1()
    rails.register(ctx)
    assert ctx.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="hi") is None


def test_on_task_is_counted_and_shown(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1()
    assert call(rails, ctx) is None
    assert ctx.shown == ["⚖ scope (watch only): 1 on task"]


def test_side_quest_would_block_but_shadow_never_blocks(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1("side_quest", 0.8)
    assert call(rails, ctx, args={"command": "npm install left-pad"}) is None
    assert "1 would block: terminal `npm install left-pad`" in ctx.shown[-1]


def test_unsure_side_quest_is_not_a_would_block(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1("side_quest", 0.5)
    call(rails, ctx)
    assert ctx.shown[-1] == "⚖ scope (watch only): 1 on task"


def test_look_only_ask_flags_any_change_even_on_task(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1("on_task", 0.61, intent="look", p_intent=0.9)
    call(rails, ctx)  # running the tests: not a change
    call(rails, ctx, tool="patch", args={"path": "README.md"})  # an edit nobody asked for
    assert ctx.shown[-1] == "⚖ scope (watch only), look-only ask: 1 on task · 1 would block: patch `README.md`"
    entry = json.loads(rails.LOG.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["why"] == "changes something, but Willie only asked to look"


def test_look_only_ask_lets_a_d1_judged_change_through_when_unsure(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look", changes=0.4)
    call(rails, ctx, args={"command": "python -m pytest -q tests"})
    assert "would block" not in ctx.shown[-1]


def test_look_only_terminal_change_needs_d1_to_be_very_sure(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look", changes=0.85)  # d1 rates even running tests this high
    call(rails, ctx, args={"command": "python -m pytest -v tests"})
    assert "would block" not in ctx.shown[-1]
    rails.ask_d1 = fake_d1(intent="look", changes=0.95)
    call(rails, ctx, args={"command": "pip install pytest"})
    assert "1 would block: terminal `pip install pytest`" in ctx.shown[-1]


@pytest.mark.parametrize("command", [
    'ls -la "G:/tools/rails/bait" 2>/dev/null || echo "--- path not found"',
    "cd /g/tools/d1-guard && python -m pytest -q tests 2>&1 | tail -5",
    "grep -rn bait ~/logs | head -10",
    "git status && git log --oneline -3",
    'Get-ChildItem G:\\tools | Select-Object Name',
    "find . -name '*.py' | wc -l",
])
def test_read_only_commands_never_count_as_changes(tmp_path, command):
    rails = load(tmp_path)
    assert rails.read_only(command)


@pytest.mark.parametrize("command", [
    "pip install pytest",
    "sed -i 's/a/b/' x.py",
    "echo hi > notes.txt",
    "cat a >> b",
    "find . -name '*.pyc' -delete",
    "git commit -m wip",
    "git branch -D old",
    "git config user.name x",
    "python fix.py",
    "rm x",
    "env FOO=1 ./run.sh",
])
def test_anything_else_is_left_to_d1(tmp_path, command):
    rails = load(tmp_path)
    assert not rails.read_only(command)


def test_ls_in_a_look_only_turn_is_not_flagged(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look", changes=0.95)  # d1 said a plain ls was a change
    call(rails, ctx, args={"command": "ls -la G:/tools/rails"})
    assert "would block" not in ctx.shown[-1]


def test_rabbit_hole_on_a_look_only_ask(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look")
    for i in range(rails.LOOK_STEP_BUDGET):
        call(rails, ctx, args={"command": f"grep -rn thing log{i}"})
    assert "rabbit" not in ctx.shown[-1]
    call(rails, ctx, tool="read_file", args={"path": "x"})  # reading counts as a step too
    assert ctx.shown[-1].endswith(f"would block: rabbit hole, {rails.LOOK_STEP_BUDGET + 1} steps on a look-only ask")
    call(rails, ctx, args={"command": "grep -rn more"})
    assert f"{rails.LOOK_STEP_BUDGET + 2} steps" in ctx.shown[-1] and ctx.posts == 1


def test_no_rabbit_hole_when_willie_asked_for_a_change(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="change")
    for _ in range(rails.LOOK_STEP_BUDGET + 5):
        call(rails, ctx)
    assert "rabbit" not in ctx.shown[-1]


def test_block_mode_stops_a_rabbit_hole(setup):
    rails, ctx = setup
    rails.MODE = "block"
    rails.ask_d1 = fake_d1(intent="look")
    results = [call(rails, ctx, args={"command": "grep -rn x"}) for _ in range(rails.LOOK_STEP_BUDGET + 1)]
    assert results[-2] is None and "answer him now" in results[-1]["message"]


def test_change_ask_allows_edits(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="change")
    call(rails, ctx, tool="patch", args={"path": "app/auth.py"})
    assert ctx.shown[-1] == "⚖ scope (watch only): 1 on task"


def test_intent_is_asked_once_per_turn(setup):
    rails, ctx = setup
    seen = []
    rails.ask_d1 = fake_d1(seen=seen)
    for _ in range(3):
        call(rails, ctx)
    log = [json.loads(l) for l in rails.LOG.read_text(encoding="utf-8").splitlines()]
    assert sum(1 for e in log if e.get("kind") == "intent") == 1 and len(seen) == 4


def test_one_tag_per_turn_edited_in_place(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1()
    for _ in range(3):
        call(rails, ctx)
    assert ctx.posts == 1 and ctx.edits == 2
    assert ctx.shown == ["⚖ scope (watch only): 3 on task"]


def test_d1_down_fails_open_and_loud(setup):
    rails, ctx = setup

    def down(state, questions):
        raise ConnectionRefusedError("no d1")
    rails.ask_d1 = down
    assert call(rails, ctx) is None
    assert ctx.shown[-1].startswith("❌ scope check down (2 checks failed")
    entry = json.loads(rails.LOG.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["verdict"] == "down" and "ConnectionRefusedError" in entry["error"]


def test_reading_tools_are_skipped(setup):
    rails, ctx = setup
    rails.ask_d1 = lambda state, questions: pytest.fail("d1 should not be asked")
    assert call(rails, ctx, tool="read_file", args={"path": "x"}) is None
    assert ctx.posts == 0


def test_d1_sees_the_ask_and_the_previous_reply(setup):
    rails, ctx = setup
    seen = []
    rails.ask_d1 = fake_d1(seen=seen)
    call(rails, ctx)
    assert all("fix the login test" in s and "Want me to fix it?" in s for s in seen)
    assert "test_login.py" in seen[-1]


def test_block_mode_blocks_side_quests_and_look_only_changes(setup):
    rails, ctx = setup
    rails.MODE = "block"
    rails.ask_d1 = fake_d1("side_quest", 0.9)
    assert "did not ask" in call(rails, ctx, args={"command": "npm install left-pad"})["message"]
    rails.ask_d1 = fake_d1(intent="look")
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t2", user_message="check the tests")
    result = ctx.hooks["pre_tool_call"](tool_name="patch", args={"path": "README.md"}, session_id="s", turn_id="t2")
    assert result["action"] == "block" and "only asked to look" in result["message"]


def test_log_masks_long_tokens(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1()
    call(rails, ctx, args={"command": "curl -H 'x: fake_token_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456' x"})
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in rails.LOG.read_text(encoding="utf-8")

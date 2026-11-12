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
        self.idx = len(ctx.shown)
        ctx.shown.append(text)

    def edit(self, text):
        # Edit the message this handle posted, the way Hermes reuses a notification key.
        self.ctx.shown[self.idx] = text
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


def fake_d1(scope="on_task", p=0.9, intent="change", p_intent=0.9, changes=0.1, seen=None, kind="other", p_kind=0.9,
            on_track="serving", p_on_track=0.9):
    def ask(state, questions):
        if seen is not None:
            seen.append(state)
        if "kind" in questions:
            return {"kind": _choice(kind, p_kind, ("bug", "feature", "plan", "other"))}
        if "intent" in questions:
            return {"intent": _choice(intent, p_intent, ("look", "change", "unclear"))}
        if "on_track" in questions:
            return {"on_track": _choice(on_track, p_on_track, ("serving", "wandering"))}
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
    assert ctx.shown == ["Rails: 1 passed, 0 blocked"]


def test_side_quest_would_block_but_shadow_never_blocks(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1("side_quest", 0.8)
    assert call(rails, ctx, args={"command": "npm install left-pad"}) is None
    assert ctx.shown[-1] == "Rails: 0 passed, 1 blocked"


def test_unsure_side_quest_is_not_a_would_block(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1("side_quest", 0.5)
    call(rails, ctx)
    assert ctx.shown[-1] == "Rails: 1 passed, 0 blocked"


def test_look_only_ask_flags_any_change_even_on_task(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1("on_task", 0.61, intent="look", p_intent=0.9)
    call(rails, ctx)  # running the tests: not a change
    call(rails, ctx, tool="patch", args={"path": "README.md"})  # an edit nobody asked for
    assert ctx.shown[-1] == "Rails: 1 passed, 1 blocked"
    entry = json.loads(rails.LOG.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["why"] == "changes something, but the user only asked to look"


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
    assert ctx.shown[-1] == "Rails: 1 passed, 1 blocked"


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


def test_repeated_inventory_is_a_rabbit_hole_early(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look")
    # Five ls/pwd-style commands on a look-only ask trip the inventory-tour signal, before any step cap.
    for command in ("ls", "pwd", "dir", "whoami", "hostname"):
        call(rails, ctx, args={"command": command})
    assert ctx.shown[-1] == "Rails: 4 passed, 1 blocked"


def test_long_focused_reads_are_not_a_rabbit_hole_when_on_track(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look", on_track="serving", p_on_track=0.95)
    for i in range(12):
        call(rails, ctx, tool="read_file", args={"path": f"app/auth{i}.py"})
    # read_file is skipped for scope, but the on-track check should keep this as serving.
    assert "rabbit" not in (ctx.shown[-1] if ctx.shown else "")


def test_d1_wandering_flags_a_rabbit_hole(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="look", on_track="wandering", p_on_track=0.9)
    # Four unrelated greps: no inventory signal, but the periodic on-track check starts at step 4.
    for i in range(4):
        call(rails, ctx, args={"command": f"grep -rn unrelated{i} docs"})
    assert "blocked" in ctx.shown[-1] and "1 blocked" in ctx.shown[-1]


def test_no_rabbit_hole_when_the_user_asked_for_a_change(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="change")
    for _ in range(rails.LOOK_STEP_BUDGET + 5):
        call(rails, ctx)
    assert "rabbit" not in ctx.shown[-1]


def test_block_mode_stops_a_rabbit_hole(setup):
    rails, ctx = setup
    rails.MODE = "block"
    rails.ask_d1 = fake_d1(intent="look")
    results = [call(rails, ctx, args={"command": cmd}) for cmd in ("ls", "pwd", "dir", "whoami", "hostname")]
    assert results[-2] is None and "answer the user now" in results[-1]["message"]


def test_change_ask_allows_edits(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1(intent="change")
    call(rails, ctx, tool="patch", args={"path": "app/auth.py"})
    assert ctx.shown[-1] == "Rails: 1 passed, 0 blocked"


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
    assert ctx.shown == ["Rails: 3 passed, 0 blocked"]


def test_d1_down_fails_open_and_loud(setup):
    rails, ctx = setup

    def down(state, questions):
        raise ConnectionRefusedError("no d1")
    rails.ask_d1 = down
    assert call(rails, ctx) is None
    assert ctx.shown[-1] == "Rails: 0 passed, 0 blocked"
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


def test_status_notice_uses_passed_and_blocked_totals(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1()
    call(rails, ctx)
    rails.ask_d1 = fake_d1("side_quest", 0.9)
    call(rails, ctx, args={"command": "npm install left-pad"})
    rails.ask_d1 = fake_d1()
    call(rails, ctx)
    assert ctx.posts == 1 and ctx.edits == 2
    assert ctx.shown == ["Rails: 2 passed, 1 blocked"]


def test_end_of_turn_rewrites_the_same_notice_not_a_new_one(setup):
    rails, ctx = setup
    rails.ask_d1 = fake_d1()
    for _ in range(3):
        call(rails, ctx)
    ctx.hooks["post_llm_call"](session_id="s")
    assert ctx.posts == 1 and ctx.edits == 3  # two running updates + one final summary
    assert ctx.shown == ["Rails: 3 passed, 0 blocked"]


def test_failed_edit_does_not_post_another_notice(setup):
    """If the surface cannot take an edit, still do not stack a second post_message."""
    rails, ctx = setup
    rails.ask_d1 = fake_d1()

    class BrokenHandle:
        def __init__(self):
            self.ok, self.error = True, None

        def edit(self, text):
            return {"ok": False, "error": "fallback_would_stack"}

    def first_post(text):
        ctx.posts += 1
        ctx.shown.append(text)
        return BrokenHandle()

    ctx.post_message = first_post
    call(rails, ctx)
    call(rails, ctx)
    call(rails, ctx)
    ctx.hooks["post_llm_call"](session_id="s")
    assert ctx.posts == 1 and ctx.shown == ["Rails: 1 passed, 0 blocked"]


@pytest.mark.parametrize("command", [
    "git checkout -- app/auth.py",
    "git reset --hard HEAD",
    "git clean -fd",
    "git push origin main",
    "git merge feature",
    "git rebase main",
    "rm -rf node_modules",
    "del /f temp.txt",
    "Remove-Item -Recurse build",
])
def test_mutating_commands_are_always_changes(tmp_path, command):
    rails = load(tmp_path)
    assert rails.mutating(command)
    assert not rails.read_only(command)


def test_git_status_is_not_mutating(tmp_path):
    rails = load(tmp_path)
    assert not rails.mutating("git status")
    assert rails.read_only("git status")


def test_look_only_git_checkout_is_blocked_even_when_d1_is_unsure(tmp_path):
    rails = load(tmp_path)
    result = rails.judge("Don't change anything, just tell me why login fails.", "",
                         "terminal", {"command": "git checkout -- app/auth.py"},
                         look_only=True, ask_fn=fake_d1(intent="look", changes=0.2, scope="on_task"))
    assert result["p_change"] == 1.0 and result["would_block"]


def test_scratch_write_is_not_blocked_as_a_side_quest(tmp_path):
    rails = load(tmp_path)
    result = rails.judge("What does the LICENSE file say? Don't change anything.", "",
                         "write_file",
                         {"path": r"C:\Users\willi\AppData\Local\Temp\hermes-scratch-notes.txt", "content": "MIT"},
                         look_only=True, ask_fn=fake_d1(scope="side_quest", p=0.9, intent="look"))
    assert result["p_change"] == 0.0 and not result["would_block"]


def test_off_target_click_includes_find_in_the_scope_state(tmp_path):
    rails = load(tmp_path)
    seen = []
    finds = [{"target": "the Settings button", "found": True, "click_at": [200, 80],
              "box_px": {"left": 160, "top": 60, "right": 240, "bottom": 100}}]
    rails.judge("Open Settings.", "", "computer_use",
                {"action": "click", "coordinate": [1100, 20]},
                look_only=False, ask_fn=fake_d1(seen=seen, scope="side_quest", p=0.9),
                finds=finds)
    state = seen[-1]
    assert "1100" in state and "20" in state
    assert "Settings" in state
    assert "does not land" in state
    assert "top-right" in state


def test_wander_after_answer_is_a_rabbit_hole(tmp_path):
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "What's in config.yaml?",
        [{"tool": "read_file", "args": {"path": "config.yaml"}}]
        + [{"tool": "read_file", "args": {"path": f"docs/unrelated{i}.md"}} for i in range(3)],
        look_only=True, check_model=False)
    assert hole["rabbit"] and "wander_after_answer" in hole["signals"]


def test_rereading_named_files_is_not_wander_after_answer(tmp_path):
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "Compare README.md and LICENSE.",
        ([{"tool": "read_file", "args": {"path": "README.md"}},
          {"tool": "read_file", "args": {"path": "LICENSE"}}] * 5),
        look_only=True, check_model=False)
    assert not hole["rabbit"] and "wander_after_answer" not in hole["signals"]


def test_login_stack_tour_is_not_wander_after_answer(tmp_path):
    """A focused auth/login tour should not look like wandering just because
    the ask named the topic, not every file."""
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "Explain how login works. Don't change files.",
        [{"tool": "read_file", "args": {"path": p}} for p in (
            "app/auth.py", "app/models/user.py", "app/models/session.py",
            "app/routes/login.py", "app/templates/login.html",
            "tests/test_login.py", "tests/conftest.py", "docs/auth.md")],
        look_only=True, check_model=False)
    assert not hole["rabbit"] and "wander_after_answer" not in hole["signals"]


def test_payment_stack_tour_is_not_wander_after_answer(tmp_path):
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "Explain how payments work. Don't change files.",
        [{"tool": "read_file", "args": {"path": p}} for p in (
            "app/payments.py", "app/stripe.py", "app/webhooks.py",
            "app/invoices.py", "app/models/charge.py", "app/models/refund.py",
            "tests/test_payments.py", "docs/payments.md",
            "app/receipts.py", "app/taxes.py")],
        look_only=True, check_model=False)
    assert not hole["rabbit"] and "wander_after_answer" not in hole["signals"]


def test_unrelated_docs_after_named_file_still_wander(tmp_path):
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "Compare README.md and LICENSE.",
        [{"tool": "read_file", "args": {"path": "README.md"}},
         {"tool": "read_file", "args": {"path": "LICENSE"}}]
        + [{"tool": "read_file", "args": {"path": f"docs/{i}.md"}} for i in range(10)],
        look_only=True, check_model=False)
    assert hole["rabbit"] and "wander_after_answer" in hole["signals"]


def test_unrelated_heads_after_config_still_wander(tmp_path):
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "What's in config.yaml?",
        [{"tool": "read_file", "args": {"path": "config.yaml"}}]
        + [{"tool": "terminal", "args": {"command": f"head -n 20 file{i}.txt"}} for i in range(9)],
        look_only=True, check_model=False)
    assert hole["rabbit"] and "wander_after_answer" in hole["signals"]


def test_git_tour_is_a_rabbit_hole(tmp_path):
    rails = load(tmp_path)
    cmds = ["git log -10 --oneline", "git stash list", "git branch -a", "git remote -v",
            "git fetch", "git blame LICENSE"]
    hole = rails.rabbit_hole(
        "Show me recent commits.",
        [{"tool": "terminal", "args": {"command": c}} for c in cmds],
        look_only=True, check_model=False)
    assert hole["rabbit"] and "git_tour" in hole["signals"]


def test_two_git_log_commands_are_not_a_git_tour(tmp_path):
    rails = load(tmp_path)
    hole = rails.rabbit_hole(
        "Show me recent commits.",
        [{"tool": "terminal", "args": {"command": "git log -10 --oneline"}},
         {"tool": "terminal", "args": {"command": "git log -1 --stat"}}],
        look_only=True, check_model=False)
    assert not hole["rabbit"] and "git_tour" not in hole["signals"]


def test_off_target_click_is_blocked_even_when_d1_says_on_task(tmp_path):
    rails = load(tmp_path)
    finds = [{"target": "the Settings button", "found": True, "click_at": [200, 80],
              "box_px": {"left": 160, "top": 60, "right": 240, "bottom": 100}}]
    result = rails.judge("Open Settings.", "", "computer_use",
                         {"action": "click", "coordinate": [1240, 18]},
                         look_only=False, ask_fn=fake_d1(scope="on_task", p=0.95, changes=0.2),
                         finds=finds)
    assert result["would_block"] and result["why"] == "side quest" and result["verdict"] == "side_quest"


def test_on_target_click_is_not_forced_to_a_side_quest(tmp_path):
    rails = load(tmp_path)
    finds = [{"target": "the Settings button", "found": True, "click_at": [200, 80],
              "box_px": {"left": 160, "top": 60, "right": 240, "bottom": 100}}]
    result = rails.judge("Open Settings.", "", "computer_use",
                         {"action": "click", "coordinate": [200, 80]},
                         look_only=False, ask_fn=fake_d1(scope="on_task", p=0.95, changes=0.2),
                         finds=finds)
    assert not result["would_block"] and result["verdict"] == "on_task"


def test_top_right_close_click_on_open_settings_is_a_side_quest(tmp_path):
    rails = load(tmp_path)
    result = rails.judge("Open Settings with computer use.", "", "computer_use",
                         {"action": "click", "coordinate": [1100, 20]},
                         look_only=False, ask_fn=fake_d1(scope="on_task", p=0.95, changes=0.2))
    assert result["would_block"] and result["why"] == "side quest"


def test_top_right_click_is_allowed_when_the_ask_is_to_close(tmp_path):
    rails = load(tmp_path)
    result = rails.judge("Close the Settings window.", "", "computer_use",
                         {"action": "click", "coordinate": [1100, 20]},
                         look_only=False, ask_fn=fake_d1(scope="on_task", p=0.95, changes=0.2))
    assert not result["would_block"]


def test_run_the_tests_and_tell_me_is_look(tmp_path):
    rails = load(tmp_path)
    found = rails.intent("Run the tests and tell me if they pass.", "",
                         ask_fn=fake_d1(intent="change", p_intent=0.9))
    assert found["intent"] == "look" and found["p"] >= rails.LOOK_ONLY_AT
    found = rails.intent("Fix the failing login test.", "",
                         ask_fn=fake_d1(intent="change", p_intent=0.9))
    assert found["intent"] == "change"

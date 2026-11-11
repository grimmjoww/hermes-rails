"""Rails workflow gate (step 3), with stand-ins for d1 and Hermes. Run: python -m pytest -q tests"""
import json

import pytest

from test_rails import Ctx, fake_d1, load


@pytest.fixture
def rails_ctx(tmp_path):
    rails, ctx = load(tmp_path), Ctx()
    skills = tmp_path / "superpowers" / "skills"
    for name in ("systematic-debugging", "test-driven-development", "brainstorming"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    rails.SUPERPOWERS_DIR = tmp_path / "superpowers"
    rails.register(ctx)
    return rails, ctx


def turn(rails, ctx, kind="bug", message="the login test is failing, fix it", p_kind=0.9, session="s", turn_id="t1"):
    rails.ask_d1 = fake_d1(kind=kind, p_kind=p_kind)
    return ctx.hooks["pre_llm_call"](session_id=session, turn_id=turn_id, user_message=message)


def tool(ctx, name, args, session="s"):
    return ctx.hooks["pre_tool_call"](tool_name=name, args=args, session_id=session, turn_id="t1")


def verify(ctx, paths, session="s", attempt=0):
    return ctx.hooks["pre_verify"](session_id=session, attempt=attempt, changed_paths=paths)


def test_bug_turn_names_the_debugging_skill(rails_ctx):
    rails, ctx = rails_ctx
    result = turn(rails, ctx)
    assert 'skill_view("superpowers:systematic-debugging")' in result["context"]
    assert "not a message from Willie" in result["context"]
    assert ctx.shown[-1] == "⚖ workflow: bug fix (0.90) → load superpowers:systematic-debugging before changing anything"


@pytest.mark.parametrize("kind,skill", [("feature", "test-driven-development"), ("plan", "brainstorming")])
def test_each_kind_gets_its_skill(rails_ctx, kind, skill):
    rails, ctx = rails_ctx
    assert f"superpowers:{skill}" in turn(rails, ctx, kind=kind)["context"]


def test_other_turns_get_nothing(rails_ctx):
    rails, ctx = rails_ctx
    assert turn(rails, ctx, kind="other", message="how was your day babe") is None
    assert ctx.posts == 0


def test_unsure_kind_gets_nothing(rails_ctx):
    rails, ctx = rails_ctx
    assert turn(rails, ctx, p_kind=0.5) is None


def test_changes_are_held_until_the_skill_is_loaded(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    assert tool(ctx, "read_file", {"path": "app/auth.py"}) is None                 # investigating is fine
    assert tool(ctx, "terminal", {"command": "pytest tests/test_login.py"}) is None  # running tests is fine
    held = tool(ctx, "patch", {"path": "app/auth.py"})
    assert held["action"] == "block" and "superpowers:systematic-debugging" in held["message"]
    assert ctx.shown[-1].startswith("⛔ workflow: held patch")
    assert tool(ctx, "skill_view", {"name": "superpowers:systematic-debugging"}) is None
    assert ctx.shown[-1] == "✅ workflow: bug fix → superpowers:systematic-debugging loaded"
    assert tool(ctx, "patch", {"path": "app/auth.py"}) is None


def test_reading_a_linked_file_does_not_count_as_loading(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    tool(ctx, "skill_view", {"name": "superpowers:systematic-debugging", "file_path": "references/x.md"})
    assert tool(ctx, "patch", {"path": "app/auth.py"})["action"] == "block"


def test_skill_loaded_earlier_in_the_chat_is_not_required_again(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    tool(ctx, "skill_view", {"name": "superpowers:systematic-debugging"})
    assert turn(rails, ctx, turn_id="t2") is None
    assert ctx.shown[-1] == "✅ workflow: bug fix → superpowers:systematic-debugging (loaded earlier this chat)"
    assert tool(ctx, "patch", {"path": "app/auth.py"}) is None


def test_d1_down_fails_open_and_loud(rails_ctx):
    rails, ctx = rails_ctx

    def down(state, questions):
        raise ConnectionRefusedError("no d1")
    rails.ask_d1 = down
    assert ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="fix the login test") is None
    assert ctx.shown[-1].startswith("❌ workflow check down")


def test_done_needs_a_test_after_code_changes(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    result = verify(ctx, ["G:/app/auth.py"])
    assert result["action"] == "continue" and "no test ran" in result["message"]
    assert ctx.shown[-1].startswith("⛔ can't finish yet: code changed (auth.py)")
    tool(ctx, "terminal", {"command": "python -m pytest -q tests/test_login.py"})
    assert verify(ctx, ["G:/app/auth.py"], attempt=1) is None
    assert ctx.shown[-1].startswith("✅ proof: test ran (terminal `python -m pytest -q tests/test_login.py`)")


@pytest.mark.parametrize("command", [
    "cd /g/projects/tip-split && python -m unittest -v test_tip_split",
    'cd "C:/Users/willi/INCOME" && python selftest4.py 2>&1 | tail -30',
    'cd "C:/x" && "C:/Users/willi/AppData/Local/Programs/Python/Python313/python.exe" test_demo_server.py 2>&1',
    "python3 tests/smoke_test.py",
    "npm test",
    "pytest -q",
])
def test_test_runs_are_recognised(rails_ctx, command):
    rails, ctx = rails_ctx
    assert rails.TEST_COMMAND.search(command)


@pytest.mark.parametrize("command", ["python tip_split.py --bill 80", "python inspect2.py", "cat test_tip_split.py",
                                     "python apply_update.py"])
def test_other_commands_are_not_test_runs(rails_ctx, command):
    rails, ctx = rails_ctx
    assert not rails.TEST_COMMAND.search(command)


def test_skill_load_is_logged(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    tool(ctx, "skill_view", {"name": "superpowers:systematic-debugging"})
    entries = [json.loads(l) for l in rails.LOG.read_text(encoding="utf-8").splitlines()]
    assert {"kind": "skill_loaded", "skill": "superpowers:systematic-debugging"}.items() <= entries[-1].items()


def test_non_code_edits_need_no_test(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx, kind="other", message="fix the typo in the readme")
    assert verify(ctx, ["G:/app/README.md", "G:/notes.txt"]) is None


def test_proof_check_fails_closed(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)

    def boom(paths):
        raise RuntimeError("broken")
    rails._code_paths = boom
    assert "proof check itself failed" in verify(ctx, ["G:/app/auth.py"])["message"]


def test_shadow_mode_only_shows(rails_ctx):
    rails, ctx = rails_ctx
    rails.WORKFLOW_MODE = "shadow"
    assert turn(rails, ctx) is None
    assert "would require superpowers:systematic-debugging" in ctx.shown[-1]
    assert tool(ctx, "patch", {"path": "app/auth.py"}) is None
    assert verify(ctx, ["G:/app/auth.py"]) is None


def test_off_mode_does_nothing(rails_ctx):
    rails, ctx = rails_ctx
    rails.WORKFLOW_MODE = "off"
    assert turn(rails, ctx) is None and verify(ctx, ["G:/app/auth.py"]) is None and ctx.posts == 0


def test_without_superpowers_falls_back_to_hermes_bundled_skills(rails_ctx, tmp_path):
    rails, ctx = rails_ctx
    rails.SUPERPOWERS_DIR = tmp_path / "missing"
    assert 'skill_view("systematic-debugging")' in turn(rails, ctx)["context"]
    assert turn(rails, ctx, kind="plan", turn_id="t2") is None  # no bundled brainstorming
    assert "no skill installed" in ctx.shown[-1]


def test_decisions_are_logged(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    tool(ctx, "patch", {"path": "app/auth.py"})
    verify(ctx, ["G:/app/auth.py"])
    kinds = [json.loads(l).get("kind") for l in rails.LOG.read_text(encoding="utf-8").splitlines()]
    assert {"workflow", "workflow_block", "verify"} <= set(kinds)


# --- switchyard's skill-routing line joins the same toast (one toast per turn, not two) ---

def test_routing_line_joins_the_workflow_toast(rails_ctx):
    rails, ctx = rails_ctx
    turn(rails, ctx)
    assert rails.note_skill("s", "t1", "🧭 Skill loaded: ue4ss-lua-modding") is True
    assert ctx.shown[-1].endswith("before changing anything · 🧭 Skill loaded: ue4ss-lua-modding")
    assert ctx.posts == 1


def test_routing_line_that_arrives_first_shows_at_turn_start(rails_ctx):
    rails, ctx = rails_ctx
    rails.note_skill("s", "t1", "🧭 No skill needed (0.2 s)")
    turn(rails, ctx, kind="other", message="how was your day babe")
    assert ctx.shown[-1] == "🧭 No skill needed (0.2 s)"
    assert ctx.posts == 1


def test_an_old_turns_routing_line_is_not_shown(rails_ctx):
    rails, ctx = rails_ctx
    rails.note_skill("s", "t0", "🧭 Skill loaded: pdf")
    turn(rails, ctx, kind="other", message="how was your day babe")
    assert ctx.posts == 0


# --- vet_skill: on a turn Rails claims, switchyard may add domain knowledge, never a second working method ---

def _with_domain(rails, kind, p_domain, calls=None):
    base = fake_d1(kind=kind)

    def ask(state, questions):
        if calls is not None:
            calls.append(next(iter(questions)))
        if "domain" in questions:
            return {"domain": {"noul": p_domain}}
        return base(state, questions)
    rails.ask_d1 = ask


def test_a_method_skill_is_refused_on_a_bug_turn(rails_ctx):
    rails, ctx = rails_ctx
    _with_domain(rails, "bug", 0.23)
    assert rails.vet_skill("s", "t1", "my script throws keyerror, debug it", [], "agent-introspection-debugging", "x") is False


def test_a_domain_skill_is_kept_on_a_bug_turn(rails_ctx):
    rails, ctx = rails_ctx
    _with_domain(rails, "bug", 0.93)
    assert rails.vet_skill("s", "t1", "the ue4ss mod crashes on load", [], "ue-savegame-mapping", "x") is True


def test_turns_rails_does_not_claim_keep_any_skill_without_asking(rails_ctx):
    rails, ctx = rails_ctx
    calls = []
    _with_domain(rails, "other", 0.0, calls)
    assert rails.vet_skill("s", "t1", "find me remote jobs", [], "job-search:assistant", "x") is True
    assert "domain" not in calls


def test_the_turn_is_classified_once_for_both_plugins(rails_ctx):
    rails, ctx = rails_ctx
    calls = []
    _with_domain(rails, "bug", 0.9, calls)
    rails.vet_skill("s", "t1", "the login test is failing, fix it", [], "pdf", "x")
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="the login test is failing, fix it")
    assert calls.count("kind") == 1


def test_vet_fails_open_when_d1_is_down(rails_ctx):
    rails, ctx = rails_ctx

    def down(state, questions):
        raise OSError("refused")
    rails.ask_d1 = down
    assert rails.vet_skill("s", "t1", "fix the login test", [], "orch-add-feature", "x") is True


def test_watch_only_mode_never_refuses(rails_ctx):
    rails, ctx = rails_ctx
    rails.WORKFLOW_MODE = "shadow"
    _with_domain(rails, "bug", 0.1)
    assert rails.vet_skill("s", "t1", "fix the login test", [], "tdd-workflow", "x") is True


def test_a_skills_verdict_is_remembered(rails_ctx):
    rails, ctx = rails_ctx
    calls = []
    _with_domain(rails, "bug", 0.1, calls)
    rails.vet_skill("s", "t1", "fix the login test", [], "tdd-workflow", "x")
    rails.vet_skill("s", "t2", "fix the signup test", [], "tdd-workflow", "x")
    assert calls.count("domain") == 1


# --- prefetch_turn: switchyard gives Rails a head start on d1's second slot ---

def test_a_head_start_means_the_turn_asks_d1_once(rails_ctx):
    import threading
    rails, ctx = rails_ctx
    asked, gate = [], threading.Event()
    inner = fake_d1(kind="bug", p_kind=0.9)

    def slow(state, questions):
        if "kind" in questions:
            asked.append("kind")
            gate.wait(5)
        return inner(state, questions)
    rails.ask_d1 = slow
    rails.prefetch_turn("s", "t1", "the login test is failing, fix it", [])
    threading.Timer(0.2, gate.set).start()  # the hook arrives while the head start is still asking
    result = ctx.hooks["pre_llm_call"](session_id="s", turn_id="t1", user_message="the login test is failing, fix it")
    assert asked == ["kind"]
    assert 'skill_view("superpowers:systematic-debugging")' in result["context"]


def test_no_head_start_for_short_asks_or_when_off(rails_ctx):
    rails, _ = rails_ctx
    asked = []
    rails.ask_d1 = lambda state, questions: asked.append(1) or fake_d1()(state, questions)
    rails.prefetch_turn("s", "t1", "ok", [])
    rails.WORKFLOW_MODE = "off"
    rails.prefetch_turn("s", "t2", "the login test is failing, fix it", [])
    assert asked == []

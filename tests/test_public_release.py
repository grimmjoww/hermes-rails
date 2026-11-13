"""Catalog-review checks: no hardcoded names in model-facing text, safe computer-use defaults."""
from __future__ import annotations

import re
from pathlib import Path

from test_eyes import _cua_setup, _do, CAPTURE
from test_rails import load

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"\bWillie\b|\bRei\b", re.IGNORECASE)
CREDIT_OK = re.compile(
    r"author:\s*\"grimmjoww\"|github\.com/grimmjoww|Copyright \(c\) 2026 Phantom Horizons Studios \(grimmjoww\)",
    re.IGNORECASE,
)
MODEL_FACING_FILES = ("__init__.py", "eyes.py", "eyes_tools.py", "plugin.yaml")


def _model_facing_blobs(rails):
    blobs = []
    for name in ("INTENT", "SCOPE", "KIND", "ON_TRACK", "INTENT_QUESTIONS", "CALL_QUESTIONS",
                 "KIND_QUESTIONS", "ON_TRACK_QUESTIONS"):
        blobs.append(repr(getattr(rails, name)))
    blobs.append(rails._ask_block("please look at README", "ok"))
    blobs.append(rails.harness_note("example"))
    blobs.append(rails.judge.__doc__ or "")
    # plugin.yaml description + setting descriptions (author line excluded later)
    yaml_text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    blobs.append(yaml_text)
    blobs.append((ROOT / "eyes_tools.py").read_text(encoding="utf-8"))
    blobs.append((ROOT / "eyes.py").read_text(encoding="utf-8"))
    return blobs


def test_default_labels_are_generic(tmp_path):
    rails = load(tmp_path)
    assert rails.USER == "the user"
    assert rails.AGENT == "the agent"
    assert "Willie" not in rails.harness_note("hello")
    assert "Rei" not in rails.harness_note("hello")
    assert "the user" in rails.harness_note("hello")


def test_model_facing_strings_do_not_name_willie_or_rei(tmp_path):
    rails = load(tmp_path)
    hits = []
    for blob in _model_facing_blobs(rails):
        for i, line in enumerate(blob.splitlines(), 1):
            if CREDIT_OK.search(line) and not NAME.search(CREDIT_OK.sub("", line)):
                continue
            if NAME.search(line):
                hits.append(f"{line.strip()}")
    # also harvest live messages from CUA / scope helpers
    ask = rails._ask_block("check the file", "")
    if NAME.search(ask):
        hits.append(ask)
    assert hits == [], "model-facing text still names a person:\n" + "\n".join(hits)


def test_source_model_facing_files_have_no_hardcoded_names():
    """Repo grep of model-facing sources (author/credit lines are allowed)."""
    hits = []
    for name in MODEL_FACING_FILES:
        path = ROOT / name
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CREDIT_OK.search(line) and not NAME.search(CREDIT_OK.sub("", line)):
                continue
            if NAME.search(line):
                hits.append(f"{name}:{i}: {line.rstrip()}")
    assert hits == [], "hardcoded names in model-facing sources:\n" + "\n".join(hits)


def test_computer_use_mode_defaults_to_shadow(tmp_path):
    rails = load(tmp_path)
    assert rails.CUA_MODE == "shadow"
    yaml_text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    block = yaml_text.split("computer_use_mode:", 1)[1].split("second_opinion_mode:", 1)[0]
    assert 'default: shadow' in block or 'default: "shadow"' in block
    assert "0.4.2" in yaml_text.split("version", 1)[1][:40]


def test_click_hold_is_skipped_when_d3_is_down(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, mode="on", vision=False,
                                   ask="can you use computer use and click the search box")
    _do(ctx, "computer_use", CAPTURE[1], CAPTURE[2])
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert not (held and "computer-use gate" in (held or {}).get("message", "")), held
    # and the turn-start steer must not tell the model to stop
    assert "and stop" not in (steer or {}).get("context", "")


def test_click_hold_still_runs_when_d3_is_up(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, mode="on", vision=True)
    _do(ctx, "computer_use", CAPTURE[1], CAPTURE[2])
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held and held["action"] == "block" and "screen_find" in held["message"]


def test_jev_is_mentioned_in_block_text_only_when_registered(tmp_path, monkeypatch):
    rails, ctx, steer = _cua_setup(tmp_path, monkeypatch, mode="on", vision=True)
    _do(ctx, "computer_use", CAPTURE[1], CAPTURE[2])
    held = _do(ctx, "computer_use", {"action": "click", "element": 6})
    assert held and "jev_computer_use" not in held["message"]

    rails2, ctx2, steer2 = _cua_setup(tmp_path, monkeypatch, mode="on", vision=True)
    ctx2.tools = ["jev_computer_use", "computer_use", "screen_find"]
    _do(ctx2, "computer_use", CAPTURE[1], CAPTURE[2])
    held2 = _do(ctx2, "computer_use", {"action": "click", "element": 6})
    assert held2 and "jev_computer_use" in held2["message"]

    # stand-in hold also stays quiet about jev when it is not registered
    rails3, ctx3, steer3 = _cua_setup(tmp_path, monkeypatch, mode="on", vision=True)
    held3 = ctx3.hooks["pre_tool_call"](tool_name="browser_exec", args={"goal": "amazon"},
                                       session_id="s", turn_id="t1")
    assert held3 and held3["action"] == "block" and "jev_computer_use" not in held3["message"]

    rails4, ctx4, steer4 = _cua_setup(tmp_path, monkeypatch, mode="on", vision=True)
    ctx4.tools = ["jev_computer_use"]
    held4 = ctx4.hooks["pre_tool_call"](tool_name="browser_exec", args={"goal": "amazon"},
                                       session_id="s", turn_id="t1")
    assert held4 and "jev_computer_use" in held4["message"]

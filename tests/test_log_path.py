"""The decision log path must survive a plugin file that is not two directories deep."""
from pathlib import Path

from test_rails import load


def test_deep_install_logs_under_the_hermes_home(tmp_path):
    rails = load(tmp_path)
    plugin = Path("/home/me/.hermes/plugins/rails/__init__.py")
    resolved = plugin.resolve()
    assert rails.log_path(plugin) == resolved.parents[2] / "logs" / "rails.jsonl"


def test_shallow_plugin_path_does_not_raise(tmp_path):
    rails = load(tmp_path)
    plugin = Path("/rails_plugin.py")
    resolved = plugin.resolve()
    assert len(resolved.parents) <= 2
    assert rails.log_path(plugin) == resolved.parents[0] / "logs" / "rails.jsonl"

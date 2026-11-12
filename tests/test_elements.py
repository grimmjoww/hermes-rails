"""Numbered text lists from the element payloads Hermes tools already return."""
import importlib.util
import json
import sys
from pathlib import Path

from test_rails import fake_d1, load
from test_settings import SettingsCtx

HERE = Path(__file__).resolve().parents[1]


def elements():
    name = "rails_elements_under_test"
    spec = importlib.util.spec_from_file_location(name, HERE / "elements.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CART = {"index": 6, "role": "AXButton", "label": "Add to cart", "bounds": [412, 300, 108, 36], "app": "chrome"}
SEARCH = {"index": 7, "role": "AXTextField", "label": "Search", "bounds": None, "app": "chrome"}


def test_computer_use_elements_become_a_numbered_list():
    mod = elements()
    payload = {
        "mode": "som", "width": 1456, "height": 869, "app": "chrome", "window_title": "Shop",
        "elements": [CART, SEARCH],
        "total_elements": 2,
        "summary": "  #1 AXButton 'decoy from the summary' @ (1, 1, 2, 2)",
    }
    assert mod.element_list(payload) == "\n".join((
        '[6] button "Add to cart" (412,300,520,336)',
        '[7] textbox "Search"',
    ))
    found = {row.index: row for row in mod.targets(payload)}
    assert found[6].source_id == "6"
    assert found[7].box is None


def test_structured_content_frames_and_tokens():
    mod = elements()
    payload = {"data": '[1] AXButton "ignored markdown"', "structuredContent": {"elements": [
        {"element_index": 1, "role": "AXButton", "label": "OK",
         "frame": {"x": 10, "y": 20, "w": 80, "h": 30}, "element_token": "snap2:1"},
        {"element_index": 2, "role": "AXTextField", "label": "search",
         "frame": {"x": 100, "y": 50, "w": 200, "h": 24}, "element_token": "snap2:2"},
    ]}}
    rows = mod.targets(payload)
    assert [(row.index, row.role, row.name, row.box, row.token) for row in rows] == [
        (1, "button", "OK", (10, 20, 90, 50), "snap2:1"),
        (2, "textbox", "search", (100, 50, 300, 74), "snap2:2"),
    ]
    assert "ignored markdown" not in mod.element_list(payload)


def test_capture_summary_lines_and_unknown_bounds():
    mod = elements()
    text = "\n".join((
        "capture mode=som 1456x869",
        "2 interactable element(s):",
        "  #6 AXButton 'Add to cart' @ (412, 300, 108, 36)",
        "  #7 AXTextField 'Search' @ bounds-unknown (click by element index)",
    ))
    assert mod.element_list(text) == "\n".join((
        '[6] button "Add to cart" (412,300,520,336)',
        '[7] textbox "Search"',
    ))


def test_ax_tree_markdown_keeps_clickable_roles_and_real_labels():
    """Line forms cua-driver actually emits (quoted, id=, paren, = \"value\")."""
    mod = elements()
    tree = "\n".join((
        '  - [14] AXButton "One"',
        '  - [15] AXButton "Two"',
        "  - [16] AXTextField \"\"",
        "[14] AXButton (1) id=One",
        "- [77] AXButton (Auto) [help=\"...\" actions=[press]]",
        '- [79] AXButton (Dark) [help="Use a dark appearance..." actions=[press]]',
        '          - [4] AXStaticText = "Wi-Fi" [id=com.apple.wifi actions=[showmenu]]',
        '- [92] AXPopUpButton = "Automatic" [id=HighlightColorPicker actions=[press]]',
        "- [100] AXRadioButton (Always) [actions=[press]]",
        "[200] AXButton (5) id=RealLabel",
        "[201] AXButton (7)",
    ))
    text = mod.element_list(tree)
    assert '[14] button "One"' in text
    assert '[77] button "Auto"' in text
    assert '[79] button "Dark"' in text
    assert "Wi-Fi" not in text
    assert '[92] combobox "Automatic"' in text
    assert '[100] radio "Always"' in text
    assert '[200] button "RealLabel"' in text
    assert "[201] button\n" in text + "\n"
    # The duplicate [14] from the id= line keeps the first label.
    assert text.splitlines()[0] == '[14] button "One"'


def test_cdp_accessibility_nodes_use_backend_id_and_box_quad():
    mod = elements()
    tree = {"nodes": [
        {"nodeId": "1", "ignored": False, "role": {"type": "role", "value": "generic"},
         "name": {"type": "computedString", "value": "wrapper"}, "backendDOMNodeId": 1},
        {"nodeId": "2", "ignored": True, "role": {"type": "role", "value": "button"},
         "name": {"type": "computedString", "value": "Skip"}, "backendDOMNodeId": 2},
        {"nodeId": "3", "ignored": False, "role": {"type": "role", "value": "button"},
         "name": {"type": "computedString", "value": "Add to cart"}, "backendDOMNodeId": 42,
         "properties": [{"name": "disabled", "value": {"type": "booleanOrUndefined", "value": False}}],
         "boxModel": {"content": [412, 300, 520, 300, 520, 336, 412, 336], "width": 108, "height": 36}},
        {"nodeId": "4", "ignored": False, "role": {"type": "role", "value": "textbox"},
         "name": {"type": "computedString", "value": "Email"},
         "value": {"type": "string", "value": "ada@example.com"}, "backendDOMNodeId": 43,
         "properties": [{"name": "disabled", "value": {"type": "booleanOrUndefined", "value": True}}]},
        {"nodeId": "5", "ignored": False, "role": {"type": "role", "value": "checkbox"},
         "name": {"type": "computedString", "value": "Remember me"}, "backendDOMNodeId": 44,
         "properties": [
             {"name": "hidden", "value": {"type": "booleanOrUndefined", "value": True}},
             {"name": "checked", "value": {"type": "booleanOrUndefined", "value": True}},
         ]},
        {"nodeId": "6", "ignored": False, "role": {"type": "role", "value": "checkbox"},
         "name": {"type": "computedString", "value": "Gift wrap"}, "backendDOMNodeId": 45,
         "properties": [{"name": "checked", "value": {"type": "booleanOrUndefined", "value": True}}]},
    ]}
    assert mod.element_list(tree) == "\n".join((
        '[42] button "Add to cart" (412,300,520,336)',
        '[45] checkbox "Gift wrap" checked',
    ))
    assert mod.targets(tree)[0].source_id == "42"


def test_uia_tree_drops_offscreen_and_disabled_and_maps_automation_id():
    mod = elements()
    tree = {
        "ControlType": "Window", "Name": "Shop", "IsEnabled": True, "IsOffscreen": False,
        "Children": [
            {"ControlType": "Text", "Name": "Your cart", "AutomationId": "heading",
             "IsEnabled": True, "IsOffscreen": False},
            {"ControlType": "Button", "Name": "Add to cart", "AutomationId": "addToCart",
             "BoundingRectangle": {"Left": 412, "Top": 300, "Right": 520, "Bottom": 336},
             "IsEnabled": True, "IsOffscreen": False},
            {"ControlType": "Button", "Name": "Hidden", "AutomationId": "hidden",
             "IsEnabled": True, "IsOffscreen": True,
             "BoundingRectangle": {"left": 1, "top": 1, "right": 2, "bottom": 2}},
            {"ControlType": "Edit", "Name": "Email", "AutomationId": "email",
             "IsEnabled": False, "IsOffscreen": False, "Value": "ada@example.com",
             "BoundingRectangle": [10, 40, 200, 64]},
            {"ControlType": "CheckBox", "Name": "Gift wrap", "AutomationId": "gift",
             "IsEnabled": True, "IsOffscreen": False, "ToggleState": "On",
             "BoundingRectangle": {"x": 10, "y": 80, "width": 16, "height": 16}},
        ],
    }
    rows = mod.targets(tree)
    assert [(row.index, row.source_id, row.role, row.name, row.value) for row in rows] == [
        (1, "addToCart", "button", "Add to cart", ""),
        (2, "gift", "checkbox", "Gift wrap", "on"),
    ]
    assert mod.element_list(tree) == "\n".join((
        '[1] button "Add to cart" (412,300,520,336)',
        '[2] checkbox "Gift wrap" on (10,80,26,96)',
    ))


def test_numeric_automation_id_is_the_stable_index():
    mod = elements()
    row = mod.targets([{"ControlType": "Button", "Name": "Save", "AutomationId": "6",
                        "IsEnabled": True, "IsOffscreen": False}])[0]
    assert row.index == 6 and row.source_id == "6"


def test_nothing_usable_is_no_text_list():
    mod = elements()
    assert mod.element_list("capture mode=som 1456x869") == "no text list"
    assert mod.element_list({"elements": []}) == "no text list"
    assert mod.element_list({"nodes": [{"role": {"value": "generic"}, "name": {"value": "layout"},
                                       "backendDOMNodeId": 9}]}) == "no text list"
    assert mod.element_list({"ControlType": "Pane", "Name": "empty", "IsEnabled": True,
                             "IsOffscreen": False}) == "no text list"
    assert mod.targets(None) == []


def test_cap_keeps_source_indices_in_order():
    mod = elements()
    payload = {"elements": [
        {"index": 2, "role": "AXButton", "label": "A", "bounds": [0, 0, 0, 0]},
        {"index": 5, "role": "AXButton", "label": "B", "bounds": [1, 1, 2, 2]},
        {"index": 9, "role": "AXLink", "label": "C", "bounds": [3, 3, 4, 4]},
    ]}
    assert [row.index for row in mod.targets(payload, limit=2)] == [2, 5]
    assert "C" not in mod.element_list(payload, limit=2)


def test_json_string_and_zero_bounds_are_accepted():
    mod = elements()
    raw = json.dumps({"elements": [CART, {"index": 8, "role": "AXButton", "label": "Zero", "bounds": [0, 0, 0, 0]}]})
    text = mod.element_list(raw)
    assert text.startswith('[6] button "Add to cart" (412,300,520,336)')
    assert '[8] button "Zero"' in text
    assert "(0,0,0,0)" not in text


def test_setting_cap_is_what_the_list_uses(tmp_path):
    mod = elements()
    rails, ctx = load(tmp_path), SettingsCtx({"element_list_max": 1})
    rails.ask_d1 = fake_d1()
    rails.register(ctx)
    ctx.hooks["pre_llm_call"](session_id="s", turn_id="t", user_message="hi there")
    payload = {"elements": [CART, SEARCH]}
    assert rails.ELEMENT_LIST_MAX == 1
    assert mod.element_list(payload, limit=rails.ELEMENT_LIST_MAX) == '[6] button "Add to cart" (412,300,520,336)'
    assert mod.DEFAULT_MAX == 80

"""Turn tool element payloads into one numbered text list of clickable targets.

Rails does not route clicks through this yet. Callers pass whatever a tool
already returned. Accepted shapes, taken from Hermes Agent:

- ``computer_use`` capture JSON (``tools/computer_use/tool.py`` ``_element_to_dict``):
  ``elements: [{index, role, label, bounds, app}]``. ``bounds`` is
  ``[x, y, width, height]`` in pixels, or null when the driver has no geometry.
- cua-driver ``structuredContent.elements``: ``{element_index, role, label,
  frame: {x, y, w, h}, element_token}``.
- The capture summary lines Hermes shows the model:
  ``#6 AXButton 'Add to cart' @ (412, 300, 108, 36)`` (x, y, width, height).
- cua-driver AX-tree markdown, the same lines ``_parse_elements_from_tree``
  reads: ``[14] AXButton "One"``, ``[14] AXButton (1) id=One``,
  ``[77] AXButton (Auto)``, ``[92] AXPopUpButton = "Automatic"``.
- ``browser_exec`` CDP ``Accessibility.getFullAXTree`` ``nodes`` (role / name /
  backendDOMNodeId, role and name as AXValue ``{type, value}``). A
  ``DOM.getBoxModel`` quad may be attached as ``boxModel.content`` or
  ``model.content`` (eight numbers, x/y pairs around the border).
- A Windows UI Automation tree a helper would hand over as dicts:
  ControlType, Name, AutomationId, BoundingRectangle, IsEnabled, IsOffscreen.
  BoundingRectangle is a Win32 RECT ``{left, top, right, bottom}`` or
  ``[left, top, right, bottom]``, or ``{x, y, width, height}``. Nested
  ``children`` / ``Children`` are walked.

Offscreen and disabled controls are dropped. Raw trees (markdown, CDP, UIA)
keep clickable or typeable roles only; a ``computer_use`` ``elements`` array is
already the driver's interactable set, so those entries are kept. The number in
brackets is the source element id when that id is a positive integer (SOM
index, backendDOMNodeId, numeric AutomationId). Otherwise it is a stable
1-based index in encounter order, and ``Target.source_id`` still holds the
source id. An empty usable set is the string ``no text list``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

DEFAULT_MAX = 80
EMPTY = "no text list"
_NAME_CHARS = 120
_VALUE_CHARS = 80

# Short role written in the list, keyed by the role text with a leading "ax" removed.
_ROLES = {
    "button": "button",
    "splitbutton": "button",
    "togglebutton": "button",
    "disclosuretriangle": "button",
    "checkbox": "checkbox",
    "radio": "radio",
    "radiobutton": "radio",
    "textfield": "textbox",
    "textarea": "textbox",
    "textbox": "textbox",
    "searchbox": "textbox",
    "edit": "textbox",
    "link": "link",
    "hyperlink": "link",
    "popupbutton": "combobox",
    "combobox": "combobox",
    "listbox": "listbox",
    "slider": "slider",
    "menuitem": "menuitem",
    "menuitemcheckbox": "menuitem",
    "menuitemradio": "menuitem",
    "menubutton": "menubutton",
    "tab": "tab",
    "tabitem": "tab",
    "switch": "switch",
    "spinbutton": "spinbutton",
    "incrementor": "spinbutton",
    "spinner": "spinbutton",
    "treeitem": "treeitem",
    "listitem": "listitem",
    "dataitem": "listitem",
    "option": "option",
    "cell": "cell",
    "document": "document",
}
_INTERACTIVE = set(_ROLES.values())
_STATES = ("checked", "selected", "pressed", "expanded")

# cua-driver AX markdown. Groups: index, role, = "value", "quoted", (paren), id=.
_TREE_LINE = re.compile(
    r"^\s*(?:-\s+)?\[(\d+)\]\s+(\w+)"
    r"(?:"
    r'\s*=\s*"([^"]*)"'
    r'|\s+"([^"]*)"'
    r"|\s+\((?!\d+\))([^)]*)\)"
    r")?"
    r"(?:\s+(?:\(\d+\)\s+)?id=([^\s\[\]]+))?"
)
# Hermes capture summary: #N Role 'label' @ (x, y, w, h) or @ bounds-unknown.
_SUMMARY_LINE = re.compile(
    r"^\s*#(\d+)\s+(\w+)\s*(?:'([^']*)'|\"([^\"]*)\")?"
    r"(?:\s+@\s+(?:bounds-unknown\b|\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)))?"
)


@dataclass(frozen=True)
class Target:
    """One row of the text list. ``index`` is what the line shows; ``source_id`` is the tool's id."""

    index: int | None
    source_id: str
    role: str
    name: str
    value: str = ""
    box: tuple[int, int, int, int] | None = None
    token: str = ""


def element_list(data, *, limit: int = DEFAULT_MAX) -> str:
    """Numbered text of usable targets, or ``no text list`` when nothing is clickable."""
    rows = targets(data, limit=limit)
    if not rows:
        return EMPTY
    return "\n".join(format_target(row) for row in rows)


def targets(data, *, limit: int = DEFAULT_MAX) -> list[Target]:
    """Usable targets in source order, capped at ``limit``. Indices already used are not reused."""
    cap = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else 0
    found: list[Target] = []
    used: set[int] = set()
    nxt = 1
    for kind, payload in _collect(data):
        row = _convert(kind, payload)
        if row is None:
            continue
        index = row.index
        if index is None:
            while nxt in used:
                nxt += 1
            index = nxt
            nxt += 1
            row = Target(index, row.source_id or str(index), row.role, row.name, row.value, row.box, row.token)
        if index in used:
            continue
        used.add(index)
        found.append(row)
        if len(found) >= cap:
            break
    return found


def format_target(row: Target) -> str:
    parts = [f"[{row.index}]", row.role]
    if row.name:
        parts.append(f'"{_plain(row.name, _NAME_CHARS)}"')
    if row.value in _STATES or row.value == "on":
        parts.append(row.value)
    elif row.value:
        parts.append(f'"{_plain(row.value, _VALUE_CHARS)}"')
    if row.box:
        x1, y1, x2, y2 = row.box
        parts.append(f"({x1},{y1},{x2},{y2})")
    return " ".join(parts)


def _collect(data) -> list[tuple[str, object]]:
    data = _json_text(data)
    if data is None:
        return []
    if isinstance(data, str):
        return _from_text(data)
    if isinstance(data, list):
        out: list[tuple[str, object]] = []
        for item in data:
            out.extend(_collect(item))
        return out
    if not isinstance(data, dict):
        return []
    structured = data.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("elements"), list):
        return [("element", item) for item in structured["elements"] if isinstance(item, dict)]
    if isinstance(data.get("elements"), list):
        return [("element", item) for item in data["elements"] if isinstance(item, dict)]
    if isinstance(data.get("nodes"), list):
        return [("cdp", item) for item in data["nodes"] if isinstance(item, dict)]
    if _is_uia(data):
        return _walk_uia(data)
    if _is_cdp(data):
        return [("cdp", data)]
    if _is_element(data):
        return [("element", data)]
    for key in ("text_summary", "summary", "text", "data"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _from_text(value)
    content = data.get("content")
    if isinstance(content, list):
        texts = [part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        blob = "\n".join(part for part in texts if part.strip())
        if blob.strip():
            return _from_text(blob)
    return []


def _json_text(data):
    if not isinstance(data, str):
        return data
    text = data.strip()
    if not text.startswith(("{", "[")):
        return data
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return data


def _from_text(text: str) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for line in text.splitlines():
        summary = _SUMMARY_LINE.match(line)
        if summary:
            out.append(("summary", summary))
            continue
        tree = _TREE_LINE.match(line)
        if tree:
            out.append(("tree", tree))
    return out


def _walk_uia(node: dict, seen: set[int] | None = None) -> list[tuple[str, object]]:
    seen = seen if seen is not None else set()
    marker = id(node)
    if marker in seen:
        return []
    seen.add(marker)
    out: list[tuple[str, object]] = [("uia", node)]
    kids = node.get("children")
    if not isinstance(kids, list):
        kids = node.get("Children")
    if isinstance(kids, list):
        for kid in kids:
            if isinstance(kid, dict):
                out.extend(_walk_uia(kid, seen) if _is_uia(kid) or _has_kids(kid) else _collect(kid))
    return out


def _has_kids(node: dict) -> bool:
    return isinstance(node.get("children") or node.get("Children"), list)


def _is_element(node: dict) -> bool:
    return "index" in node or "element_index" in node


def _is_cdp(node: dict) -> bool:
    if "backendDOMNodeId" in node:
        return True
    role = node.get("role")
    return isinstance(role, dict) and "value" in role


def _is_uia(node: dict) -> bool:
    return any(key in node for key in ("ControlType", "controlType", "LocalizedControlType"))


def _convert(kind: str, payload) -> Target | None:
    if kind == "element":
        return _from_element(payload, trust_role=True)
    if kind == "summary":
        return _from_summary(payload)
    if kind == "tree":
        return _from_tree(payload)
    if kind == "cdp":
        return _from_cdp(payload)
    if kind == "uia":
        return _from_uia(payload)
    return None


def _from_element(raw: dict, *, trust_role: bool) -> Target | None:
    if _disabled(raw) or _offscreen(raw):
        return None
    index = _positive_int(raw.get("index"))
    if index is None:
        index = _positive_int(raw.get("element_index"))
    if index is None:
        return None
    role = _role(raw.get("role"), trust=trust_role)
    if role is None:
        return None
    name = _text(raw.get("label") if raw.get("label") is not None else raw.get("name"))
    value = _text(raw.get("value"))
    box = _xywh_box(raw.get("bounds"))
    if box is None and isinstance(raw.get("frame"), dict):
        box = _xywh_box([raw["frame"].get(key) for key in ("x", "y", "w", "h")])
    token = raw.get("element_token")
    return Target(index, str(index), role, name, value, box, token if isinstance(token, str) else "")


def _from_summary(match: re.Match) -> Target | None:
    role = _role(match.group(2), trust=True)
    if role is None:
        return None
    index = int(match.group(1))
    name = match.group(3) if match.group(3) is not None else (match.group(4) or "")
    box = None
    if match.group(5) is not None:
        box = _xywh_box([match.group(5), match.group(6), match.group(7), match.group(8)])
    return Target(index, str(index), role, _text(name), "", box, "")


def _from_tree(match: re.Match) -> Target | None:
    role = _role(match.group(2), trust=False)
    if role is None:
        return None
    index = int(match.group(1))
    label = match.group(3) or match.group(4) or match.group(5) or match.group(6) or ""
    return Target(index, str(index), role, _text(label), "", None, "")


def _from_cdp(node: dict) -> Target | None:
    if node.get("ignored") is True or _prop_true(node, "disabled") or _prop_true(node, "hidden"):
        return None
    role = _role(_ax_value(node.get("role")), trust=False)
    if role is None:
        return None
    index = _positive_int(node.get("backendDOMNodeId"))
    if index is None:
        index = _positive_int(node.get("nodeId"))
    source = _text(node.get("backendDOMNodeId") if node.get("backendDOMNodeId") not in (None, "") else node.get("nodeId"))
    name = _text(_ax_value(node.get("name")))
    value = _state(node) or _text(_ax_value(node.get("value")))
    if value == name:
        value = _state(node)
    box = _quad_box(node.get("boxModel")) or _quad_box(node.get("model"))
    return Target(index, source or (str(index) if index else ""), role, name, value, box, "")


def _from_uia(node: dict) -> Target | None:
    enabled = _flag(_first(node, "IsEnabled", "isEnabled"))
    offscreen = _flag(_first(node, "IsOffscreen", "isOffscreen"))
    if enabled is False or offscreen is True:
        return None
    role = _role(_first(node, "ControlType", "controlType", "LocalizedControlType"), trust=False)
    if role is None:
        return None
    auto_id = _text(_first(node, "AutomationId", "automationId"))
    name = _text(_first(node, "Name", "name")) or auto_id
    index = _positive_int(auto_id)
    value = _uia_value(node)
    box = _bounds(node.get("BoundingRectangle") if "BoundingRectangle" in node else node.get("boundingRectangle"))
    return Target(index, auto_id or name, role, name, value, box, "")


def _uia_value(node: dict) -> str:
    selected = _flag(_first(node, "IsSelected", "isSelected"))
    if selected is True:
        return "selected"
    toggle = _text(_first(node, "ToggleState", "toggleState"))
    if toggle.lower() in {"on", "checked", "1"}:
        return "on" if toggle.lower() == "on" else "checked"
    return _text(_first(node, "Value", "value"))


def _role(raw, *, trust: bool) -> str | None:
    text = _text(raw)
    if text.lower().startswith("controltype."):
        text = text.split(".", 1)[1]
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    if key.startswith("ax"):
        key = key[2:]
    short = _ROLES.get(key)
    if short:
        return short
    if trust and key:
        return key
    return None


def _state(node: dict) -> str:
    for name in _STATES:
        if _prop_true(node, name):
            return name
    return ""


def _prop_true(node: dict, name: str) -> bool:
    for prop in node.get("properties") or []:
        if not isinstance(prop, dict) or prop.get("name") != name:
            continue
        value = prop.get("value")
        if isinstance(value, dict):
            value = value.get("value")
        return _flag(value) is True
    return False


def _ax_value(raw):
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value")
    return raw


def _disabled(raw: dict) -> bool:
    if _flag(raw.get("disabled")) is True or _flag(raw.get("IsEnabled")) is False:
        return True
    return _flag(raw.get("enabled")) is False


def _offscreen(raw: dict) -> bool:
    return _flag(raw.get("offscreen")) is True or _flag(raw.get("IsOffscreen")) is True or _flag(raw.get("is_offscreen")) is True


def _bounds(raw) -> tuple[int, int, int, int] | None:
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",")]
        return _ltrb(parts) if len(parts) == 4 else None
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        return _ltrb(raw)
    if isinstance(raw, dict):
        low = {str(key).lower(): value for key, value in raw.items()}
        if all(key in low for key in ("left", "top", "right", "bottom")):
            return _ltrb([low["left"], low["top"], low["right"], low["bottom"]])
        if all(key in low for key in ("x", "y", "width", "height")):
            return _xywh_box([low["x"], low["y"], low["width"], low["height"]])
        if all(key in low for key in ("x", "y", "w", "h")):
            return _xywh_box([low["x"], low["y"], low["w"], low["h"]])
    return None


def _quad_box(raw) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if not isinstance(content, (list, tuple)) or len(content) < 8:
        return None
    try:
        xs = [int(content[i]) for i in range(0, 8, 2)]
        ys = [int(content[i]) for i in range(1, 8, 2)]
    except (TypeError, ValueError):
        return None
    return _ltrb([min(xs), min(ys), max(xs), max(ys)])


def _xywh_box(raw) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = (int(part) for part in raw)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, x + w, y + h)


def _ltrb(raw) -> tuple[int, int, int, int] | None:
    try:
        left, top, right, bottom = (int(part) for part in raw)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _positive_int(value) -> int | None:
    if isinstance(value, bool) or isinstance(value, float):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    return None


def _first(node: dict, *keys):
    for key in keys:
        if key in node:
            return node[key]
    return None


def _text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _plain(value: str, limit: int) -> str:
    return _text(value).replace('"', "'")[:limit]

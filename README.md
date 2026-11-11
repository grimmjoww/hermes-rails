# Rails — a guardrails and decision plugin for Hermes Agent

Rails keeps a Hermes agent on what you actually asked for and shows every decision it makes.
It uses a fast local "System 1" model to make quick yes/no and multiple-choice calls on each turn,
so the main model doesn't have to guess.

- **d1 Decision Guard** (d1-3B, local, about 0.2 s): decides what kind of turn this is (look-only vs. change),
  whether a tool call is work you didn't ask for, and which skill applies.
- **d3 vision** (d3-turbo, optional): looks at the newest computer-use screenshot before the agent says
  "done", and powers the `screen_find`, `screen_check` and `video_check` tools.
- **d3-ultra second opinion** (optional): consulted when d1 is under 60% sure.

Every decision is shown inline as a tag (⚖ ✅ ⛔ ❌) and written to a decision log.

## What it does

- **Scope gate** (`scope_mode`: `shadow` by default, or `block`): flags tool calls that change things when you only asked to look,
  and calls out "rabbit holes" past `look_step_budget` steps on a look-only question.
- **Workflow gate** (`workflow_mode`): code work loads the matching skill first, and "done" after code edits needs a test run.
- **Screen check** (`screen_check_mode`): after clicking/typing on screen, a vision model checks the screen before "done".
  Hermes only runs its `pre_verify` gate after file edits, so a screen-only turn is also checked at the end of the turn
  and the result is shown as a tag.
- **Computer-use gate** (`computer_use_mode`): when you ask for computer use, steers the agent to `computer_use` with d3 as its eyes.
- **Prompt-injection watch**: tool results are checked in the background; a hit is one ⚠ line and a log entry. It never blocks.
- **Tools** (toolset `decisions`): `decide`, `screen_find`, `screen_check`, `video_check`.

All settings are editable in Hermes Desktop → Settings → Plugins → Rails.

## Requirements

- Hermes Agent `>=0.22.0`
- A local d1 decision server at `http://127.0.0.1:8089` (Decision Guard). Optional: d3-turbo at `:8899`
  (Computer Use Vision) and d3-ultra at `:8898` (Decision Second Opinion). When a layer is off, Rails says
  so plainly and never fakes the answer.

## Install

Copy this folder into your Hermes plugins folder as `rails`
(on Windows: `%LOCALAPPDATA%\hermes\plugins\rails`), then restart Hermes and enable it under Settings → Plugins.

Validate it with:

```
hermes plugins validate /path/to/rails --install-deps
```

## Disclosures

- **Network:** only to the local model servers above on `127.0.0.1` (ports 8089, 8898, 8899). No third-party services, no telemetry.
- **Files:** appends a decision log to `<HERMES_HOME>/logs/rails.jsonl` (long tokens are masked). Reads the newest
  computer-use screenshot, and images/videos passed to its tools, to send them to the local vision model.
  Reads skill names from the sibling `superpowers` plugin folder if present.
- **Background threads:** short-lived daemon threads for the second-opinion and injection checks.
- **No shell commands, no stored credentials, no self-updating code.**

## Tests

```
pytest tests
```

## License

MIT — see [LICENSE](LICENSE).

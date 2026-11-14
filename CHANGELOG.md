# Changelog

## 0.4.4

- Fix `money_email_check` in `plugin.yaml`: the unquoted `on` loaded as the boolean `True`, so switching back from `shadow` to `on` did not take. The choice and default are now quoted, and a `True` value is read as `on`.
- Money check now catches "Place your order", "Purchase", "Complete purchase", "Submit order", "Confirm order", and "Order now". "Check out this video" and "Pay attention" are no longer treated as money.
- Email check now catches a lone Send, Delete, or Archive click, and Ctrl/Cmd+Enter, when the current screen looks like a mail app (Inbox, Compose, Subject, ...). Outside a mail app a lone Send is still not an email step.
- Commands inside `$(...)`, backticks, and `<(...)` are now inspected, and wrappers (`sudo`, `env`, `nohup`, `timeout`, `xargs`, leading `VAR=value`) are looked through, for the money/email check and for the read-only / mutating checks. A command with a substitution is never read-only.
- In a terminal command, `/checkout` and `/v1/payments` count as a payment call only inside a URL, so `cd src/checkout && npm test` is not held.
- Long tokens are masked in every logged string, not only the `call` line. Session and turn ids are kept.
- A turn from an already-tracked session no longer evicts another session's ask.

## 0.4.3

- Fix D1 authorization bypass for continued shell commands, including curl SMTP/SMTPS with Bash backslashes or PowerShell backticks and LF/CRLF line endings.
- Use shell-aware, linear-time continuation scanning before action detection; preserve escaped-marker and Bash CRLF command boundaries, and check supported interpretations conservatively when the shell is unknown.
- Add regression coverage for denied, uncertain, unavailable, authorized, shadow-mode, and legitimate read-only actions.

## 0.4.2

- Fix four money/email detection bypasses reported on PR #5: cached coordinate clicks, captured element clicks outside the optional computer-use gate, explicit curl SMTP/SMTPS requests, and duplicated Reply/Forward labels.
- Hold detector uncertainty/errors in on mode, retain shadow behavior, and add focused regression and legitimate-action variants.
- Document detection boundaries: this is not exhaustive action recognition or a sandbox; no real payments or email sends were used in validation.

## 0.4.1

- Add a 1200×600, center-cropped catalog card at `assets/rails-card.jpg`, preserving the existing banner.
- Patch release for the catalog image and re-pin; plugin behavior is unchanged.

## 0.4.0

- Text-first computer use. A screen can become a numbered element list (`element_list_max`, default 80). With `computer_use_driver=text_first`, d1 picks the element for the next click. A pick at 0.60 or above backs that click. An unsure pick, an empty list, or a canvas or game is aimed with `screen_find` on d3-turbo. If d3 is down, the click is not held. If d1 is down, the click path stays as it is. The end-of-turn screen check still runs, and says unavailable when d3 is down.
- Money and email check, on by default (`money_email_check`: `on` or `shadow`, no `off`). A purchase or an email send, reply, forward, delete, or archive asks d1 whether the ask authorizes that specific step. No, unsure, or d1 down holds it. This is the one gate that fails closed, and it runs on computer-use turns too, including `cloud`. Upgrading from 0.3.0 turns this on: those steps are now held until d1 says yes. `shadow` only tags.
- Cloud driver (`computer_use_driver=cloud`). A strong model may click on its own. Clicks are not held for a d1 pick or a `screen_find`. `decide`, `screen_find`, and `screen_check` stay optional. Money and email are still checked. The default driver is still `vision`, so this changes nothing until you pick `cloud`.

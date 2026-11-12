# Changelog

## 0.4.0

- Text-first computer use. A screen can become a numbered element list (`element_list_max`, default 80). With `computer_use_driver=text_first`, d1 picks the element for the next click. A pick at 0.60 or above backs that click. An unsure pick, an empty list, or a canvas or game is aimed with `screen_find` on d3-turbo. If d3 is down, the click is not held. If d1 is down, the click path stays as it is. The end-of-turn screen check still runs, and says unavailable when d3 is down.
- Money and email check, on by default (`money_email_check`: `on` or `shadow`, no `off`). A purchase or an email send, reply, forward, delete, or archive asks d1 whether the ask authorizes that specific step. No, unsure, or d1 down holds it. This is the one gate that fails closed, and it runs on computer-use turns too, including `cloud`. Upgrading from 0.3.0 turns this on: those steps are now held until d1 says yes. `shadow` only tags.
- Cloud driver (`computer_use_driver=cloud`). A strong model may click on its own. Clicks are not held for a d1 pick or a `screen_find`. `decide`, `screen_find`, and `screen_check` stay optional. Money and email are still checked. The default driver is still `vision`, so this changes nothing until you pick `cloud`.

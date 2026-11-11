# wheelguard IPC protocol

Unix stream socket at `$XDG_RUNTIME_DIR/wheelguard.sock` (mode 0600), owned by the daemon.
Messages are UTF-8 JSON objects, one per line (`\n`-terminated), both directions.

## Config (`~/.config/wheelguard/config.json`)

```json
{
  "enabled": true,
  "lock_ms": 300,
  "pre_ms": 60,
  "hold_ms": 25,
  "vendor": "3367",
  "product": "1970",
  "retry_s": 2,
  "clicklock": false,
  "clicklock_ms": 5000,
  "clicklock_mode": "repeat",
  "clicklock_rate": 10,
  "clicklock_max_s": 600,
  "clicklock_ignore_drag": true,
  "debounce_ms": 0,
  "mouse": "auto"
}
```

`vendor` / `product` pick the mouse to filter. If it isn't connected, the daemon falls back to any
other supported mouse (Endgame Gear XM2w 4k v2, Razer DeathAdder V4 Pro, Logitech G Pro X
Superlight), so the defaults work for all of them.

`mouse` chooses the mouse differently: `"auto"` (the default) is the behaviour just described;
`"vvvv:pppp"` (hex vendor and product ID, lower-case, as listed in the status `mice`) filters exactly that
mouse, any model with a scroll wheel, and does not fall back to another one: while it isn't connected the
status says "selected mouse not found". Changing `mouse` while a mouse is being filtered switches over at
once. Keyboard and consumer-control nodes are never used. If the chosen mouse's device node can't be
opened, the status error is `permission denied: /dev/input/eventN` as for any mouse.

Click-Lock: with `clicklock: true`, holding the left button for `clicklock_ms` (500 to 30000) engages
the lock. Mode `repeat` releases the button and clicks `clicklock_rate` times a second (1 to 30);
mode `hold` keeps it pressed. Letting go is ignored while locked; the next click ends the lock and is
swallowed (in `hold` mode its release lets go of the button). Pressing any other mouse button also ends
the lock (that press goes through). Switching Click-Lock off or changing the
mode ends a running lock at once.

Ignore drags: with `clicklock_ignore_drag: true` (the default) and mode `repeat`, a hold only engages
the lock if the pointer stayed roughly still: once the pointer has moved more than 400 counts
(REL_X/REL_Y, measured from where the button went down) the press can no longer engage the lock. Mode
`hold` ignores this setting and works as before.

Double-click fix: `debounce_ms` (0 = off, otherwise 10 to 150; default 0) drops a press of the left,
right or middle button that arrives less than that many ms after the same button's previous release,
and the release that belongs to it, so a worn switch's chatter reaches the desktop as one click. The
window is measured from the last release that went through. A release is only ever dropped when its own
press was dropped, so no button can stick. It works on the real button events, before Click-Lock sees
them. Each dropped press is counted in `stats.debounced`.

Safety timer: `clicklock_max_s` (0 = never, otherwise 10 to 3600; default 600) ends a running lock by
itself that long after it engaged; the button is handed back like for any other end of the lock. A
running lock is also ended when the computer suspends (the daemon notices CLOCK_BOOTTIME running ahead
of CLOCK_MONOTONIC, or its loop stalling for 15 s), and when a client sends `clicklock_stop` (the app
does so when the screen locks).

`enabled: false` = pass every wheel tick through untouched (device stays grabbed, ticks are still
reported with `kind: "pass"`). Missing keys take defaults. The daemon also notices hand edits
(mtime poll, ~2 s) and applies them live.

## History (`~/.local/share/wheelguard/history.json`, `$XDG_DATA_HOME`)

The daemon keeps daily totals of wheel ticks and blocked ticks (the same counting as `stats.ticks` and
`stats.dropped`, but not reset by `reset_stats`) for the last 90 days, as
`{"version": 1, "days": [{"date": "2026-11-09", "ticks": 5120, "dropped": 61}]}`. The file is written
atomically (like the config) at most every 5 minutes while ticks come in, when the mouse goes away and
when the daemon exits. A missing or corrupt file is not an error: it is moved aside as
`history.json.corrupt` and counting starts over. The app reads it with the `history` command and draws
the block-rate chart (and the "wheel may be wearing" hint) from it.

## Client -> daemon

| Message | Effect | Reply |
|---|---|---|
| `{"cmd": "status"}` | | one `status` event |
| `{"cmd": "subscribe"}` | start streaming `tick` and `status` events to this client | one `status` event, then the stream |
| `{"cmd": "set", "config": {...partial...}}` | validate, merge, write config.json atomically, apply live | `{"type":"ok"}` or `{"type":"error","message":"..."}`, then a `status` event to all subscribers |
| `{"cmd": "history"}` | fetch the daily wheel totals | one `{"type":"history","days":[{"date":"2026-11-09","ticks":5120,"dropped":61}, ...]}` event, oldest day first, at most 90 days (local dates; today's total is up to date) |
| `{"cmd": "mice"}` | rescan the input devices (at most every 5 s, otherwise the last scan is reused) | one `{"type":"mice","mice":[...]}` event, same list as in `status` |
| `{"cmd": "reload"}` | re-read config.json | `ok`/`error` |
| `{"cmd": "clicklock_stop"}` | end a running Click-Lock now and release any held button (nothing to do if none runs) | `ok`, then `status` to subscribers if a lock ended |
| `{"cmd": "reset_stats"}` | zero the counters | `ok`, then `status` to subscribers |

Validation: `0 < hold_ms < lock_ms`, `0 <= pre_ms <= lock_ms`, all ms values <= 2000.

## Daemon -> client

```json
{"type": "status",
 "version": "1.0.0",
 "connected": true,                 // real mouse found and grabbed
 "device": "Endgame Gear Endgame Gear HS Dongle",
 "model": "Endgame Gear XM2w 4k v2",   // friendly name of the supported mouse being filtered; null for any other mouse
 "connection": "dongle",            // "dongle", "receiver" or "cable"; null when the model is unknown
 "error": null,                     // e.g. "permission denied: /dev/input/event2" when the udev rule is missing
 "config": {...full effective config...},
 "mice": [{"name": "Endgame Gear XM2w 4k v2",   // friendly model name, else the device name
           "device": "Endgame Gear HS Dongle", "vendor": "3367", "product": "1970",
           "supported": true, "connection": "dongle"}],   // connected mice with a scroll wheel; refreshed by status/subscribe/mice (<= every 5 s)
 "stats": {"ticks": 1234, "passed": 1120, "dropped": 114, "debounced": 3, "since": 1729400000.0},
 "clicklock_active": false}         // Click-Lock is engaged right now (a status event goes out when it changes)
```

```json
{"type": "tick", "t": 1729400001.234, "sign": 1, "kind": "emit|drop|hold|pass", "held": false, "gap_ms": 12.5}
```

`sign` is the raw REL_WHEEL sign (+1 = wheel up / away from user, -1 = down). A held tick produces
`hold` first and later an `emit` or `drop` with `held: true`.

Only wheel ticks go on the wire, never pointer motion. Writes to clients are non-blocking: a client
whose send buffer is full is disconnected, so a stalled GUI can never add latency to the input loop.

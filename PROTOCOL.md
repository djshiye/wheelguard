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
  "clicklock_ignore_drag": true
}
```

`vendor` / `product` pick the mouse to filter. If it isn't connected, the daemon falls back to any
other supported mouse (Endgame Gear XM2w 4k v2, Razer DeathAdder V4 Pro, Logitech G Pro X
Superlight), so the defaults work for all of them.

Click-Lock: with `clicklock: true`, holding the left button for `clicklock_ms` (500 to 30000) engages
the lock. Mode `repeat` releases the button and clicks `clicklock_rate` times a second (1 to 30);
mode `hold` keeps it pressed. Letting go is ignored while locked; the next click ends the lock and is
swallowed (in `hold` mode its release lets go of the button). Pressing any other mouse button also ends
the lock (that press goes through). Switching Click-Lock off or changing the
mode ends a running lock at once.

Ignore drags: with `clicklock_ignore_drag: true` (the default) and mode `repeat`, a hold only engages
the lock if the pointer stayed roughly still: once the pointer has moved more than 100 counts
(REL_X/REL_Y, measured from where the button went down) the press can no longer engage the lock. Mode
`hold` ignores this setting and works as before.

Safety timer: `clicklock_max_s` (0 = never, otherwise 10 to 3600; default 600) ends a running lock by
itself that long after it engaged; the button is handed back like for any other end of the lock. A
running lock is also ended when the computer suspends (the daemon notices CLOCK_BOOTTIME running ahead
of CLOCK_MONOTONIC, or its loop stalling for 15 s), and when a client sends `clicklock_stop` (the app
does so when the screen locks).

`enabled: false` = pass every wheel tick through untouched (device stays grabbed, ticks are still
reported with `kind: "pass"`). Missing keys take defaults. The daemon also notices hand edits
(mtime poll, ~2 s) and applies them live.

## Client -> daemon

| Message | Effect | Reply |
|---|---|---|
| `{"cmd": "status"}` | | one `status` event |
| `{"cmd": "subscribe"}` | start streaming `tick` and `status` events to this client | one `status` event, then the stream |
| `{"cmd": "set", "config": {...partial...}}` | validate, merge, write config.json atomically, apply live | `{"type":"ok"}` or `{"type":"error","message":"..."}`, then a `status` event to all subscribers |
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
 "stats": {"ticks": 1234, "passed": 1120, "dropped": 114, "since": 1729400000.0},
 "clicklock_active": false}         // Click-Lock is engaged right now (a status event goes out when it changes)
```

```json
{"type": "tick", "t": 1729400001.234, "sign": 1, "kind": "emit|drop|hold|pass", "held": false, "gap_ms": 12.5}
```

`sign` is the raw REL_WHEEL sign (+1 = wheel up / away from user, -1 = down). A held tick produces
`hold` first and later an `emit` or `drop` with `held: true`.

Only wheel ticks go on the wire, never pointer motion. Writes to clients are non-blocking: a client
whose send buffer is full is disconnected, so a stalled GUI can never add latency to the input loop.

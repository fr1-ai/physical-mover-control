# CLAUDE.md

Project context for Claude. Read this first before suggesting changes.

---

## What this is

A walk-behind gas lawn mower (Toro 21") being converted into a remote-controlled — and eventually semi-autonomous — mower. Operator controls it from a phone or laptop over the internet via a relay server. The Pi on the mower runs the control logic, talks to actuators via GPIO, and enforces safety.

End goal: AI + human teleoperation hybrid for golf course / large-property mowing. Pure RC (this build) → teleop over WebRTC → autonomy with human handoff.

---

## Architecture (current target)

```
Windows laptop / phone           Cloud relay (TBD)              Raspberry Pi 5 (on mower)
───────────────────────          ─────────────────              ────────────────────────
  Web UI (HTML/JS)                WebSocket broker               mower_pi_client.py
   ↓                                  ↕                            ├── network thread (asyncio)
  command msgs ──────────────────►   ↕   ──────────────────────►  ├── watchdog thread (50ms loop)
                                                                  └── GPIO output layer (TODO)
                                                                       ↓
                                                                  Servo / actuator / relays
                                                                       ↓
                                                                  Mower hardware
```

**Safety model: dead-man heartbeat.**
- Default state of every output = SAFE (drive=0, steer=0, blade=off).
- Watchdog thread runs every 50ms, independently of network code.
- If no heartbeat received in last 200ms → all outputs forced to SAFE.
- Network failure, server crash, page freeze, software bug → mower stops within 250ms.

**Layered safety (defense in depth) — all required before blade ever spins:**
1. Pi heartbeat watchdog (in code)
2. Hardware E-stop button on the mower (mushroom switch, in series with engine kill)
3. Hardware kill on engine (separate from blade relay)
4. Pi hardware watchdog timer (reboots Pi if even watchdog thread freezes)
5. Server-side connection monitoring

---

## Hardware on hand

**Compute / sensing:**
- Raspberry Pi 5 (8GB)
- Hailo AI Hat+ (26 TOPS) — for future perception
- Pi Camera 3
- Logitech C920 webcam
- USB GPS puck (VK-162)

**Actuation:**
- Steering servo (ZOSKAY 35kg)
- Linear actuator (MYFULLY 2"/50mm) — for self-propel bail engagement
- 4-channel relay module — for blade kill / engine kill
- Automotive 12V relays + mushroom E-stop switches

**Power:**
- Anker 737 power bank (USB-C PD) — Pi rail
- 12V 7Ah SLA battery — actuator rail (separate, isolated)
- Buck converters, fuses, Wago lever-nuts, IP65 enclosure

**Radio (deprioritized for now — see Status):**
- RadioMaster TX12 transmitter (ELRS, 2.4GHz)
- RadioMaster RP4TD receiver (ELRS) — bind never completed; pivoted to web-based control

**Mower:**
- Toro 21" gas walk-behind, self-propelled

---

## Status (as of last session)

✅ Pi 5 boots, OS up to date, serial enabled (`/dev/serial0` → `ttyAMA10`)
✅ Hardware E-stop and relays in parts pile (not yet wired)
❌ ELRS bind never completed — receiver LED stuck alternating green/yellow ("searching"). Likely bind-phrase mismatch or firmware version skew. **Parked.**
🔄 **Pivoted to web-based control over internet** — operator on phone/laptop ↔ cloud relay ↔ Pi.
✅ `mower_pi_client.py` skeleton written: watchdog + heartbeat logic, no real GPIO yet (prints state changes for verification).
⏳ Next: cloud relay server, web UI, GPIO output layer, then real hardware bench tests.

---

## Build phases

| Phase | Description | Hardware moves? | Done? |
|---|---|---|---|
| 1 | Pi-side safety skeleton (watchdog + heartbeat, prints only) | No | ✅ |
| 2 | Cloud relay (WebSocket broker) | No | ⏳ |
| 3 | Web UI (phone-friendly control page) | No | ⏳ |
| 4 | End-to-end test: phone → cloud → Pi (still prints, no GPIO) | No | ⏳ |
| 5 | GPIO output layer — drive a single LED on the Pi via web button | LED only | ⏳ |
| 6 | Wire steering servo to Pi, bench-test motion | Servo, blade NOT installed | ⏳ |
| 7 | Wire linear actuator + relays | Bail / kill, blade NOT installed | ⏳ |
| 8 | Add hardware E-stop in series with engine kill | All systems, blade NOT installed | ⏳ |
| 9 | Field test on bench with engine running, blade still removed | Engine on, blade OFF | ⏳ |
| 10 | Reinstall blade. Field test in fenced empty area with spotter. | Full mower | ⏳ |

**Rule: do not skip ahead.** Every previous phase must be working reliably before the next one is attempted. The blade does not go back on until phase 10.

---

## Code conventions

- **Python 3.11+** on the Pi (Raspberry Pi OS Bookworm).
- **`gpiozero`** for GPIO (NOT `RPi.GPIO` — it doesn't fully work on Pi 5).
- **`asyncio`** for network I/O. **`threading`** for the safety watchdog (must run independent of asyncio event loop).
- **`logging` module** with timestamps to ms — never `print()` in production code paths.
- **No bare `except:`** — always catch specific exceptions. A swallowed exception in this project can hide a safety failure.
- **Output functions are idempotent** — calling `go_safe()` ten times is the same as calling it once. The watchdog calls these every 50ms.
- **State changes are logged, steady states are not.** Watchdog logs only when output state differs from last applied state.

---

## Constraints / non-negotiables

🔴 **Default = SAFE.** Any new output (motor, relay, actuator) defaults to its safe state at boot, on disconnect, on exception, on shutdown. If you can't guarantee that, don't add it.

🔴 **Watchdog thread is sacred.** Do not put network calls, file I/O, or anything that can block in the watchdog loop. It must complete a cycle in well under 50ms, every time.

🔴 **No blade engagement in software demos.** The blade relay stays physically disconnected during all testing until phase 10.

🔴 **Heartbeat timeout = 200ms.** Do not raise this without explicit discussion. The number reflects "how far can the mower travel before stopping if signal is lost" — at 0.5 m/s that's 10cm of uncommanded travel, which is the upper bound of acceptable.

🔴 **Authentication on the web UI.** Even for local testing — once this is on a server, anyone who guesses the URL can control the mower. Must require login before phase 4.

---

## File layout (target)

```
physical-mover-control/
├── CLAUDE.md                    # this file
├── README.md                    # human-facing readme
├── pi/
│   ├── mower_pi_client.py       # main Pi-side client
│   ├── outputs/                 # GPIO output drivers (servo, actuator, relays)
│   │   ├── __init__.py
│   │   ├── base.py              # OutputDriver abstract base
│   │   ├── servo.py
│   │   ├── actuator.py
│   │   └── relay.py
│   ├── safety/
│   │   ├── watchdog.py
│   │   └── heartbeat.py
│   └── requirements.txt
├── server/
│   ├── relay_server.py          # WebSocket broker
│   ├── auth.py                  # operator authentication
│   └── requirements.txt
├── web/
│   ├── index.html               # phone-friendly control UI
│   ├── controls.js
│   └── style.css
├── scripts/
│   └── deploy.sh                # git pull + restart service on Pi
└── systemd/
    └── mower-client.service     # runs mower_pi_client.py at boot
```

---

## How to talk to me

- I value **honesty over reassurance.** If a plan has a flaw, say so directly.
- I'm **new to electromechanical hardware** but comfortable with software. Explain hardware steps with more detail; assume software fluency.
- **One thing at a time.** Don't dump 10-step plans when 2 will do.
- **If I'm about to do something dangerous** (power on with miswired actuator, skip safety phase, etc.) — stop me. Don't soften it.
- **No premature optimization.** Get the dumb working version first, then make it fancy.

---

## Open questions / TODO

- [ ] Pick cloud provider for relay (DigitalOcean $6/mo droplet most likely)
- [ ] Decide on auth scheme for web UI (probably just a single shared password to start)
- [ ] Finalize GPIO pin assignments for servo / actuator / relays (current header has 4 pins used by ELRS receiver — may need to repurpose)
- [ ] Resolve ELRS bind issue eventually — keep as backup low-latency control path
- [ ] Pick video transport for camera feed (low-FPS JPEG over WS for v1, WebRTC for v2)
- [ ] Hardware E-stop wiring diagram
- [ ] Engine kill wire — locate on the Toro and document

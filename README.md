# physical-mover-control

Remote-controlled walk-behind mower. Operator on phone/laptop ↔ Railway WebSocket relay ↔ Raspberry Pi 5 on the mower.

> ⚠️ Read [CLAUDE.md](./CLAUDE.md) for the full project context, safety rules, and build phases. **Do not skip ahead. Blade does not go on until phase 10.**

---

## Architecture

```
Phone/laptop          Railway (WebSocket relay)        Raspberry Pi 5
   web UI       ◄─────────────  ▲  ─────────────►       mower_pi_client.py
   (Vercel)              wss://app.railway.app             ↓
                                                       Servo / actuator / relays
```

- **Web UI** (`web/`) — static HTML/JS hosted on Vercel
- **Relay** (`server/`) — Python WebSocket server hosted on Railway
- **Pi client** (`pi/`) — Python service running on the Pi, with watchdog safety

---

## Setup — first time

### 1. Deploy the relay to Railway

```bash
# Install Railway CLI: https://docs.railway.app/guides/cli
npm i -g @railway/cli
railway login

cd server/
railway init
railway up
```

In the Railway dashboard, set environment variables:
- `MOWER_TOKEN` — long random string (used by Pi)
- `OPERATOR_TOKEN` — different long random string (used by web UI)
- Generate both with: `openssl rand -hex 32`

Copy the generated public URL — looks like `your-app.up.railway.app`.

### 2. Deploy the web UI to Vercel

```bash
cd web/
npx vercel
```

Or just drag the `web/` folder into the Vercel dashboard.

Open the URL on your phone. Enter:
- Relay URL: `wss://your-app.up.railway.app/operator`
- Token: the `OPERATOR_TOKEN` you set

### 3. Set up the Pi

```bash
ssh physical@physical-001.local
git clone https://github.com/YOUR_USER/physical-mover-control.git
cd physical-mover-control
./scripts/setup.sh
```

Edit `.env` with your Railway URL and `MOWER_TOKEN`:
```
RELAY_URL=wss://your-app.up.railway.app/mower
MOWER_TOKEN=<the secret from Railway>
```

Start the service:
```bash
sudo systemctl start mower-client
journalctl -u mower-client -f
```

You should see the Pi connect to Railway. Open the web UI on your phone, hold a direction button, and watch the Pi log change to `OUTPUT drive=+1.00 ...`.

---

## Deploy code changes

```bash
# On laptop
git push

# On Pi
./scripts/deploy.sh
```

That's it. `deploy.sh` does `git pull` + `systemctl restart`.

---

## Status

See [CLAUDE.md](./CLAUDE.md) for the live build phase tracker.

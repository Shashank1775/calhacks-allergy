# Demo runbook

How to bring the live demo up on demo day, check it, and rehearse it. The camera, the Hailo and the whole kitchen (vision, entity store, interactions, graph, verdict, gate) run on the **Pi**: one runner serves the Orders API and `GET /state` on `:8001` and the Vision API on `:8002`. The web app runs on the **laptop**, and the screens open in the laptop's browser. The details are in [`kitchen/vision/README.md`](../kitchen/vision/README.md) (Pi setup, steps 3 to 5) and [`web/README.md`](../web/README.md).

## Once, before the day

- **The Pi:** set it up with `bash kitchen/vision/pi_setup.sh install`. Copy the model bundle with `rsync -a data/models/stock-yolov8s/ <user>@raspberrypi.local:<repo>/data/models/stock-yolov8s/`, then run the device check.
- **Zones:** calibrate `zones.json` on a still from the mounted camera. It needs the bin slots (`slot-1` to `slot-5`, matching `kitchen/orders/bins.py`), the wash, the plating spot, the pass, and the clean-plate stack and utensil rack (both `clean_stack` zones). Copy it to the Pi.

## Bring it up, in this order

1. **The runner, on the Pi.** Put every prop in view first: bins in their slots, the knife on the rack, plates on the clean stack. Then run:

   ```bash
   VISION_DETECTOR=hailo VISION_BUNDLE_DIR=data/models/stock-yolov8s \
     ORDERS_CORS_ORIGINS=http://localhost:5173 VISION_CORS_ORIGINS=http://localhost:5173 \
     .venv/bin/python tools/run_local.py --live --picamera --zones zones.json
   ```

   Wait for `run_local: LIVE from the Pi camera`. Keep this terminal in view: the gate and the voice are stand-ins for now, and they print here as `[gate] ...` and `[voice] ...` lines.

2. **The web app, on the laptop.** Use Node 24.15 or newer. Put this in `web/.env.local`, with no `WEB_MOCK_API`:

   ```
   WEB_STATE_SOURCE=local
   WEB_STATE_URL=http://<pi-ip>:8001
   WEB_ORDERS_URL=http://<pi-ip>:8001
   WEB_VISION_URL=http://<pi-ip>:8002
   ```

   ```bash
   npm --prefix web run dev
   ```

3. **The check, on the laptop.** Each line is ✓ (fine), ⚠ (works, but not demo-ready) or ✗ (broken), and every ⚠ and ✗ comes with a fix. Go on only when nothing shows ✗ (the command exits 0). It only reads: it's safe to run during the demo.

   ```bash
   uv run python tools/check_demo.py --pi http://<pi-ip>
   ```

4. **The screens:** open `http://localhost:5173/tablet` and `http://localhost:5173/expo` in the laptop's browser. The banner must not say REPLAY or FEED LOST.

## Screens on another device (the iPad)

- Vite listens on localhost only. Start it with `npm --prefix web run dev -- --host` and open `http://<laptop-ip>:5173`.
- Add that origin to both CORS settings on the Pi, comma-separated with no spaces, then restart the runner:
  - `ORDERS_CORS_ORIGINS=http://localhost:5173,http://<laptop-ip>:5173`
  - the same for `VISION_CORS_ORIGINS`.
- Check from that origin: `uv run python tools/check_demo.py --pi http://<pi-ip> --web-origin http://<laptop-ip>:5173`.
- **The iPad's browser calls the Pi directly.** An iPad on Wi-Fi can't reach a Pi wired straight to the laptop over Ethernet. Until that's settled, run every screen in the laptop's browser, or put the iPad on a network that can reach the Pi.
- Voice tickets need HTTPS on the iPad (no microphone over plain HTTP), so type them.

## Rehearsal script

Run each one start to finish. Every plate is a new plate from the clean stack: put it on the plating spot, press **start plate** on its ticket on `/expo`, and finish at the pass.

| # | Do | Expect |
|---|---|---|
| 1 | **Clean pass:** ticket "peanut allergy". The plate goes to the pass untouched | PASS on expo, and `[gate] pass` on the Pi |
| 2 | **Knife-satay hold:** ticket "peanut allergy". Dip the knife in the satay bin (`bin-1`), touch the plate, plate to the pass | HOLD on expo, with the chain from `bin-1` through the knife to the plate. The Pi prints `[voice] Hold seven, peanut` (with your table number) |
| 3 | **Wash resets the knife:** after 2, rinse the knife in the wash zone, then touch a new plate with it | The chain shows the wash. A rinse scales the knife's peanut by (1 − p), it doesn't zero it: in the scripted scene, an allergy ticket still holds (clean-confidence 0.86 against 0.95). Rehearse the ticket you'll show |
| 4 | **"Also no dairy" rechecks:** ticket "peanut allergy". Touch the plate with a clean tool from the rack that dipped in the butter (`bin-2`), and it passes. Then on the tablet, **Amend a ticket**: "also no dairy" | RECHECKING, then HOLD for milk on the new revision |
| 5 | **A tool appearing mid-shift holds:** bring a second knife into view (not from the rack) and touch a plate with it | HOLD: unknown history, until the knife is washed |

## Habits

- **Props in view in the first 3 s:** everything seen in the first 3 s after `LIVE` starts clean. Anything that shows up later has unknown history and holds.
- **New plates come from the clean stack.** A plate that appears anywhere else holds.
- **Move steadily, and keep hands off the top of tools.** A tool the tracker loses comes back with a new ID and unknown history, and holds.
- **A late touch can show PASS for a fraction of a second.** An edge that lands just after the plate reached the pass rechecks it and turns it to HOLD. Don't hand a plate over the instant it turns green.
- **If anything breaks**, rerun the check and follow its fixes. Frames stopping shows FAULT on the gate, and any failure ends in HOLD or FAULT, never PASS. After restarting the runner, put the props back in view for its first 3 s.

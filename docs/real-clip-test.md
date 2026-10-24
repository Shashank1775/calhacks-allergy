# Real-clip test: does the LSTM work on real footage?

The interaction model was trained only on simulated clips. This is the quickest honest check on real video: record a
short staged session at the real counter, write down when each thing happened, and let
`tools/real_clip_test.py` turn the video into clips (with the same shared letterbox, change mask and clip builder the
model trained on) and score them. About an afternoon, most of it recording and labeling.

## 1. Recording checklist

**Camera**
- [ ] Mounted **overhead, looking straight down**, as the real system will be (Pi Camera Module 3, a webcam or a phone on a stand). Nothing on the mount should wobble.
- [ ] The whole station in view: two or three containers, a plate, and room for hands to come in.
- [ ] Landscape, 720p or 1080p, **30 fps** (anything 15 fps or more works). Note the frame rate.
- [ ] Normal kitchen lighting. Don't add special lights; a little steam or a shadow is good, it's what the real thing sees.
- [ ] Record in takes of 2–3 minutes, so a mistake costs one take. Name them `take1.mp4`, `take2.mp4`, ...

**Props**
- [ ] Containers the detector has a chance with: **bowls** work best (COCO's "bowl"); hotel pans and bins may not be found, and then you type their box in the sheet (step 2).
- [ ] Food you can see move: rice, chopped vegetables, a thick sauce.
- [ ] Tools: **a spoon, a knife, a fork** (the detector knows these). Tongs and ladles are fine too, but expect misses.
- [ ] One or two people's hands in shot.

**What to stage** (about 100 events; leave 3–5 seconds of stillness between events so they're easy to find)

| Kind | How many | What to do |
|---|---|---|
| `scoop` | 15 | Dip a spoon into a container and lift food out |
| `deposit` | 10 | Carry food on a spoon and leave it in a container or on the plate |
| `wipe` | 10 | Spread or stir food inside a container with a tool |
| `touch` | 15 | Tap a container's rim or side with a tool; no food moves |
| `hover` | 10 | Hold a tool 1–2 cm over the food for a second without touching, then leave |
| `pass_over` | 15 | Move a tool across over a container without stopping |
| `motion_nearby` | 10 | Rest your tool next to a container while someone else stirs inside it |
| `approach_leave` | 15 | Bring a tool close to a container (not over it) and take it away |

Do about a third of the `scoop`, `touch` and `approach_leave` events with a **bare hand** instead of a tool.

## 2. Labeling sheet

Copy `tools/real_clip_labels_template.csv` next to your videos and fill one row per event. Play the video with a
time display (VLC or QuickTime; step frame by frame with `.`/`,` in VLC), and for each event write down:

| Column | What to write | Example |
|---|---|---|
| `event` | A short unique name | `s01` |
| `video` | The file name, in the same folder as the sheet | `take1.mp4` |
| `t_s` | **Seconds into the video when the tool or hand touches** (for no-touch kinds: when it's closest) | `12.4` |
| `kind` | One of the eight kinds above, or `other` | `scoop` |
| `actor` | `tool` or `hand` | `tool` |
| `touched` | `y` if it physically touched the container or its food, else `n` | `y` |
| `moved` | `y` if food visibly moved between them, else `n` | `y` |
| `container_box` | Leave empty. Fill only if the preview shows the wrong or no container: `x1 y1 x2 y2` as fractions of the frame (0 to 1), read off the grid in the preview | `0.42 0.30 0.61 0.55` |
| `notes` | Anything odd | `food stuck to spoon` |

Label what really happened, not the kind's name: if a "hover" actually brushed the food, write `touched = y`.
A real hover stays `touched = n` on the sheet, but the scorer still counts it as **contact**. The camera looks
straight down and can't see that 1–2 cm, so the clip contract and the sim both call a tool resting over the food
contact, and that leans toward HOLD. A `pass_over` that never stops is not contact.

## 3. Run it

```bash
uv sync --all-extras

# First, just build the clips and previews (fast, no torch) and check the boxes:
uv run --all-extras python tools/real_clip_test.py --labels path/to/labels.csv --out data/real_clips/check --no-score

# Then score them with the LSTM (torch is fetched for this one run):
uv run --all-extras --with torch python tools/real_clip_test.py \
    --labels path/to/labels.csv --out data/real_clips/take1
```

The video needs at least 16 fps. The tool takes every Nth frame so the gaps stay even (every 2nd frame at 30 fps), and
it refuses an event whose one-second window runs off the start or end of the video, so leave a second either side.

Defaults: the detector bundle in `data/models/stock-yolov8s`, the LSTM in `data/models/lstm/boxpooled-v1/best.pt`, and
thresholds 0.17 (contact) and 0.085 (moved), the 99%-recall picks from validation. Change them with `--bundle`,
`--weights` and `--thresholds`.

## 4. Read the results

In `data/real_clips/take1/`:
- **`previews/<event>.png`**: the frames around each event with the tool's box (red), the container's box (blue) and
  the change mask. **Check a handful first**: if the boxes are on the wrong things, the score says nothing about the
  LSTM. Fix those rows with `container_box` and rerun.
- **`predictions.csv`**: one row per event: your labels (plus `contact`: touched, or a hover), the model's P(contact) and P(moved), its calls, and how many
  of the 16 steps had a box for each side.
- **`summary.json`** and the printed table: accuracy, recall (real cases caught) and false alarms per kind, at the
  thresholds and at 0.5, next to a plain "the boxes overlapped" rule for comparison.
- **`clips/`**: the clips themselves (`interaction_clip` records with labels from you), ready to fine-tune on later.

**How to judge it.** The model must catch real transfers (high recall on `scoop`, `deposit`, `wipe`) and beat the
box-overlap rule; under the "rules stay as the fallback" safety rule it replaces zone rules only once it beats them
on real clips. Events where the detector found no tool or container are listed separately: those are a detector
problem to fix in vision, not a verdict on the LSTM.

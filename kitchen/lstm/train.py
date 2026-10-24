"""Train the ConvGRU interaction model (kitchen/lstm/model.py) on sim training clips, on a GPU.

Owner: partner (human). Agents never edit this file. (Platform wrote the first version as tools/train_lstm.py.)
Step:  LSTM classifier
Reads: a clip dataset from kitchen/sim/training_clips.py; a model class from kitchen/lstm/model.py
Emits: <out>/best.pt and last.pt (weights plus the chosen thresholds), metrics.jsonl (one line per epoch), summary.json
Rules: 6 object-blind; 7 trains only on train, picks the checkpoint and the operating thresholds on val, scores test
       once at the end, and never touches the frozen held-out benchmark

A missed transfer is the unsafe mistake, so each output's operating threshold is the highest one that still catches
--target-recall (default 99%) of val's real cases. Test is reported at those thresholds and at 0.5.

Needs torch and numpy (the project doesn't install torch). From the repo root:
    pip install torch numpy
    python -m kitchen.lstm.train --data data/clips/v1 --out runs/lstm-v1 --epochs 20
"""

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import torch
from torch import nn

from kitchen.lstm import model as models

HEADS = ("contact", "transferred")  # the model's two outputs, in order


def load_split(data_dir: Path, split: str, limit: int | None) -> dict:
    """Load a split into memory: uint8 clips [N, T, C, H, W], dt_ms [N, T], labels [N, 2] and each clip's kind."""
    lines = (data_dir / split / "clips.jsonl").read_text().splitlines()[:limit]
    records = [json.loads(line) for line in lines]
    shape = records[0]["shape"]
    clips = np.empty((len(records), shape["T"], shape["C"], shape["H"], shape["W"]), dtype=np.uint8)
    dt_ms = np.empty((len(records), shape["T"]), dtype=np.float32)

    def load(index: int) -> None:
        # Each worker writes straight into the shared array, so the split is never held twice.
        with np.load(data_dir / records[index]["tensor"]["file"]) as clip:
            clips[index] = clip["x"]
            dt_ms[index] = clip["dt_ms"]

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(load, range(len(records))))
    return {
        "x": torch.from_numpy(clips),
        "dt": torch.from_numpy(dt_ms),
        "y": torch.tensor([[r["labels"][head] for head in HEADS] for r in records], dtype=torch.float32),
        "kinds": [r["generator"]["scenario_id"].split("-", 2)[2] for r in records],
    }


def augment(x: torch.Tensor) -> torch.Tensor:
    """Random flips and quarter turns of a whole batch: interaction doesn't depend on which way the camera faces."""
    if torch.rand(()) < 0.5:
        x = x.flip(-1)
    if torch.rand(()) < 0.5:
        x = x.flip(-2)
    return torch.rot90(x, int(torch.randint(0, 4, ())), dims=(-2, -1))


def average_ranks(scores: np.ndarray) -> np.ndarray:
    """1-based ranks, with tied scores sharing the average of their ranks."""
    order = np.argsort(scores, kind="stable")
    sorted_scores = scores[order]
    _, first, counts = np.unique(sorted_scores, return_index=True, return_counts=True)
    shared = first + (counts + 1) / 2  # the average rank of each run of ties
    ranks = np.empty(len(scores))
    ranks[order] = np.repeat(shared, counts)
    return ranks


def auroc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Area under the ROC curve: the chance a random positive scores above a random negative (ties count half)."""
    positives = labels.astype(bool)
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = average_ranks(scores)
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


@torch.no_grad()
def predict(model: nn.Module, data: dict, device: str, batch: int) -> np.ndarray:
    """Float32 logits [N, 2] for a split. No bf16 here, so scores keep enough precision to rank and threshold."""
    model.eval()
    logits = []
    for start in range(0, len(data["y"]), batch):
        x = data["x"][start : start + batch].to(device).float() / 255
        dt = data["dt"][start : start + batch].to(device)
        logits.append(model(x, dt).float().cpu())
    return torch.cat(logits).numpy()


def threshold_for_recall(probabilities: np.ndarray, labels: np.ndarray, target: float) -> float:
    """The highest threshold that still catches `target` of the real cases (probability >= threshold counts)."""
    positives = np.sort(probabilities[labels.astype(bool)])
    if len(positives) == 0:
        return 0.5
    allowed_misses = int(math.floor((1 - target) * len(positives)))
    return float(positives[allowed_misses])


def score(probabilities: np.ndarray, logits: np.ndarray, data: dict, thresholds: tuple[float, float]) -> dict:
    """Accuracy, recall and false-alarm rate per output at the given thresholds, overall and per episode kind."""
    labels = data["y"].numpy().astype(bool)
    kinds = np.array(data["kinds"])
    flagged = probabilities >= np.array(thresholds)
    metrics = {}
    for i, head in enumerate(HEADS):
        truth, call = labels[:, i], flagged[:, i]
        metrics[head] = {
            "threshold": thresholds[i],
            "accuracy": float((call == truth).mean()),
            "recall": float(call[truth].mean()) if truth.any() else float("nan"),
            "false_alarm": float(call[~truth].mean()) if (~truth).any() else float("nan"),
            "auroc": auroc(logits[:, i], truth),
        }
        metrics[head]["by_kind"] = {
            kind: round(float((call[kinds == kind] == truth[kinds == kind]).mean()), 4) for kind in sorted(set(kinds))
        }
    return metrics


def train_epoch(model, train: dict, optimizer, schedule, loss_fn, args) -> float:
    """One pass over train in a random order; returns the mean loss."""
    model.train()
    order = torch.randperm(len(train["y"]))
    steps = math.ceil(len(order) / args.batch)
    total = 0.0
    for step in range(steps):
        rows = order[step * args.batch : (step + 1) * args.batch]
        x = augment(train["x"][rows].to(args.device, non_blocking=True).float() / 255)
        dt = train["dt"][rows].to(args.device)
        y = train["y"][rows].to(args.device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.device.startswith("cuda")):
            logits = model(x, dt)
        loss = loss_fn(logits.float(), y)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        schedule.step()
        total += loss.item()
    return total / steps


def parse_args() -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Train the partner's interaction model on sim clips.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="InteractionNet", help="a model class in kitchen/lstm/model.py")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--target-recall", type=float, default=0.99)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None, help="use only the first N clips per split (smoke tests)")
    parser.add_argument("--device", default="cuda")
    return parser.parse_args()


def main() -> None:
    """Train, keep the checkpoint with the best val loss, pick thresholds on val, then score test once."""
    args = parse_args()
    if (args.out / "metrics.jsonl").exists():
        raise SystemExit(f"{args.out} already has a run; pick a new --out so runs don't mix")
    args.out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    settings = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}

    started = time.time()
    splits = {split: load_split(args.data, split, args.limit) for split in ("train", "val", "test")}
    print(f"loaded {[len(s['y']) for s in splits.values()]} clips in {time.time() - started:.0f} s", flush=True)

    model = getattr(models, args.model)().to(args.device)
    print(f"{args.model}: {sum(p.numel() for p in model.parameters())} parameters", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    steps_per_epoch = math.ceil(len(splits["train"]["y"]) / args.batch)
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=args.lr, total_steps=args.epochs * steps_per_epoch, pct_start=0.1
    )
    loss_fn = nn.BCEWithLogitsLoss()  # the partner's note: train with BCEWithLogitsLoss
    best_val = float("inf")

    for epoch in range(1, args.epochs + 1):
        epoch_start = time.time()
        train_loss = train_epoch(model, splits["train"], optimizer, schedule, loss_fn, args)
        logits = predict(model, splits["val"], args.device, args.batch * 2)
        val_loss = float(loss_fn(torch.from_numpy(logits), splits["val"]["y"]))
        val = score(1 / (1 + np.exp(-logits)), logits, splits["val"], (0.5, 0.5))
        line = {"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss, "val": val}
        line["seconds"] = round(time.time() - epoch_start)
        with open(args.out / "metrics.jsonl", "a") as log:
            log.write(json.dumps(line) + "\n")
        print(
            f"epoch {epoch:2d}  train {train_loss:.4f}  val {val_loss:.4f}  "
            f"contact acc {val['contact']['accuracy']:.3f} auroc {val['contact']['auroc']:.3f}  "
            f"moved acc {val['transferred']['accuracy']:.3f} auroc {val['transferred']['auroc']:.3f}  "
            f"({line['seconds']} s)",
            flush=True,
        )
        checkpoint = {"model": model.state_dict(), "epoch": epoch, "args": settings}
        torch.save(checkpoint, args.out / "last.pt")
        if val_loss < best_val:
            best_val = val_loss
            torch.save(checkpoint, args.out / "best.pt")

    # Pick the operating thresholds on val with the best checkpoint, then score test once at them and at 0.5.
    best = torch.load(args.out / "best.pt", weights_only=False)
    model.load_state_dict(best["model"])
    val_logits = predict(model, splits["val"], args.device, args.batch * 2)
    val_probabilities = 1 / (1 + np.exp(-val_logits))
    val_labels = splits["val"]["y"].numpy()
    thresholds = tuple(
        threshold_for_recall(val_probabilities[:, i], val_labels[:, i], args.target_recall) for i in range(len(HEADS))
    )
    best["thresholds"] = dict(zip(HEADS, thresholds, strict=True))
    torch.save(best, args.out / "best.pt")

    test_logits = predict(model, splits["test"], args.device, args.batch * 2)
    test_probabilities = 1 / (1 + np.exp(-test_logits))
    summary = {
        "best_epoch": best["epoch"],
        "best_val_loss": best_val,
        "target_recall": args.target_recall,
        "thresholds": best["thresholds"],
        "test_at_thresholds": score(test_probabilities, test_logits, splits["test"], thresholds),
        "test_at_0.5": score(test_probabilities, test_logits, splits["test"], (0.5, 0.5)),
        "minutes": round((time.time() - started) / 60, 1),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("thresholds", summary["thresholds"], "| test", json.dumps(summary["test_at_thresholds"]), flush=True)


if __name__ == "__main__":
    main()

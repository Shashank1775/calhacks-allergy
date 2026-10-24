"""Train the partner's ConvGRU interaction model (kitchen/lstm/model.py) on sim training clips, on a GPU.

Owner: platform (agents), standing in until the partner's kitchen/lstm/train.py exists
Step:  ConvLSTM training
Reads: a clip dataset from kitchen/sim/training_clips.py; a model class from the partner's kitchen/lstm/model.py, unchanged
Emits: <out>/best.pt and last.pt, metrics.jsonl (one line per epoch), summary.json (the best checkpoint on test)
Rules: trains only on train, picks the checkpoint on val, scores test once at the end; never edits the partner's model

Needs torch, which the project doesn't install. Run from the repo root:
    python tools/train_lstm.py --data data/clips/v1 --out runs/lstm-v1 --epochs 20
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

    def load(record: dict) -> tuple[np.ndarray, np.ndarray]:
        with np.load(data_dir / record["tensor"]["file"]) as clip:
            return clip["x"], clip["dt_ms"]

    with ThreadPoolExecutor(16) as pool:
        loaded = list(pool.map(load, records))
    return {
        "x": torch.from_numpy(np.stack([clip for clip, _ in loaded])),
        "dt": torch.from_numpy(np.stack([dt for _, dt in loaded]).astype(np.float32)),
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


def auroc(scores: torch.Tensor, labels: torch.Tensor) -> float:
    """Area under the ROC curve: the chance a random positive scores above a random negative."""
    positives = labels.bool()
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = torch.empty(len(scores))
    ranks[torch.argsort(scores)] = torch.arange(1, len(scores) + 1, dtype=torch.float32)
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


@torch.no_grad()
def evaluate(model: nn.Module, data: dict, device: str, batch: int) -> dict:
    """Loss, accuracy, AUROC and recall per head, plus accuracy per episode kind."""
    model.eval()
    logits = []
    for start in range(0, len(data["y"]), batch):
        x = data["x"][start : start + batch].to(device).float() / 255
        dt = data["dt"][start : start + batch].to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.startswith("cuda")):
            logits.append(model(x, dt).float().cpu())
    logits = torch.cat(logits)
    y = data["y"]
    probabilities = torch.sigmoid(logits)
    correct = (probabilities > 0.5) == y.bool()

    metrics = {"loss": float(nn.functional.binary_cross_entropy_with_logits(logits, y))}
    for i, head in enumerate(HEADS):
        positives = y[:, i].bool()
        metrics[f"{head}_accuracy"] = float(correct[:, i].float().mean())
        metrics[f"{head}_auroc"] = auroc(probabilities[:, i], y[:, i])
        metrics[f"{head}_recall"] = float(correct[positives, i].float().mean()) if positives.any() else float("nan")

    by_kind = {}
    for kind in sorted(set(data["kinds"])):
        rows = torch.tensor([k == kind for k in data["kinds"]])
        by_kind[kind] = {head: round(float(correct[rows, i].float().mean()), 4) for i, head in enumerate(HEADS)}
    metrics["by_kind"] = by_kind
    return metrics


def main() -> None:
    """Train, keep the checkpoint with the best val loss, then score it on test."""
    parser = argparse.ArgumentParser(description="Train the partner's interaction model on sim clips.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None, help="use only the first N clips per split (smoke tests)")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--model", default="InteractionNet", help="a model class in kitchen/lstm/model.py")
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    splits = {split: load_split(args.data, split, args.limit) for split in ("train", "val", "test")}
    print(f"loaded {[len(s['y']) for s in splits.values()]} clips in {time.time() - started:.0f} s", flush=True)

    train = splits["train"]
    model = getattr(models, args.model)().to(args.device)
    print(f"{args.model}: {sum(p.numel() for p in model.parameters())} parameters", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    steps_per_epoch = math.ceil(len(train["y"]) / args.batch)
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=args.lr, total_steps=args.epochs * steps_per_epoch, pct_start=0.1
    )
    loss_fn = nn.BCEWithLogitsLoss()  # the partner's note: train with BCEWithLogitsLoss
    best_val = float("inf")

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_start, total = time.time(), 0.0
        order = torch.randperm(len(train["y"]))
        for step in range(steps_per_epoch):
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

        val = evaluate(model, splits["val"], args.device, args.batch * 2)
        line = {"epoch": epoch, "train_loss": total / steps_per_epoch, "seconds": round(time.time() - epoch_start)}
        line.update({f"val_{key}": value for key, value in val.items()})
        with open(args.out / "metrics.jsonl", "a") as log:
            log.write(json.dumps(line) + "\n")
        print(
            f"epoch {epoch:2d}  train {line['train_loss']:.4f}  val {val['loss']:.4f}  "
            f"contact acc {val['contact_accuracy']:.3f} auroc {val['contact_auroc']:.3f}  "
            f"moved acc {val['transferred_accuracy']:.3f} auroc {val['transferred_auroc']:.3f}  "
            f"({line['seconds']} s)",
            flush=True,
        )

        checkpoint = {"model": model.state_dict(), "epoch": epoch, "args": vars(args) | {"data": str(args.data)}}
        torch.save(checkpoint, args.out / "last.pt")
        if val["loss"] < best_val:
            best_val = val["loss"]
            torch.save(checkpoint, args.out / "best.pt")

    model.load_state_dict(torch.load(args.out / "best.pt", weights_only=False)["model"])
    test = evaluate(model, splits["test"], args.device, args.batch * 2)
    summary = {"best_val_loss": best_val, "test": test, "minutes": round((time.time() - started) / 60, 1)}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("test", json.dumps(test), flush=True)


if __name__ == "__main__":
    main()

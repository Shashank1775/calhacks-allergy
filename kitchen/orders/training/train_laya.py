"""Fine-tune Laya on ticket notes with laya.train.finetune. Runs on a GPU machine (we use pina1, the lab's server).

Owner: orders (agents)
Step:  orders (training the ticket-note Laya model)
Reads: a data folder with train.jsonl and val.jsonl from build_dataset.py; the base checkpoint (convaiinnovations/laya)
Emits: a fine-tuned checkpoint folder plus train_summary.json
Rules: weights never go in git (data/ is gitignored); pick the GPU with CUDA_VISIBLE_DEVICES

    CUDA_VISIBLE_DEVICES=2 python train_laya.py --data ~/laya/data/full --out ~/laya/runs/v1
"""

import argparse
import json
import time
from pathlib import Path

from laya.train import TrainConfig, finetune


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="folder with train.jsonl and val.jsonl")
    parser.add_argument("--base", default=str(Path.home() / "laya" / "laya_base"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--micro-batch", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--loss", choices=["rlcd", "soft-ce"], default="rlcd")
    args = parser.parse_args()

    # Defaults follow the published notebook (effective batch 64); val is used for before/after checks.
    config = TrainConfig(
        epochs=args.epochs,
        micro_batch=args.micro_batch,
        grad_accum=args.grad_accum,
        loss=args.loss,
        eval_data=str(Path(args.data) / "val.jsonl"),
        seed=42,
    )

    started = time.time()
    summary = finetune(str(Path(args.data) / "train.jsonl"), args.base, args.out, config, device="cuda")
    summary["seconds"] = round(time.time() - started)
    summary["args"] = vars(args)

    Path(args.out, "train_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"done in {summary['seconds']} s; summary at {args.out}/train_summary.json")


if __name__ == "__main__":
    main()

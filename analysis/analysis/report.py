"""CLI: ``python -m analysis.report <run_dir>... --labels a,b --out analysis/out/<name>``."""

import argparse
import json
from pathlib import Path

from analysis.figures import plot_comparison, plot_run
from analysis.load import load_run
from analysis.metrics import summarize


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="+")
    p.add_argument("--labels", default="")
    p.add_argument("--out", default="analysis/out/report")
    args = p.parse_args()

    runs = [load_run(d) for d in args.runs]
    labels = args.labels.split(",") if args.labels else [r.scenario["name"] for r in runs]
    if len(labels) != len(runs):
        p.error("--labels must have one label per run")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if len(runs) == 1:
        plot_run(runs[0], out)
    else:
        plot_comparison(runs, labels, out)
        for r, lab in zip(runs, labels):
            plot_run(r, out / lab)
    summary = {lab: summarize(r) for lab, r in zip(labels, runs)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

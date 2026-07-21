#!/usr/bin/env python3
"""Run the versioned rear-brake profile-inference evaluation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api" / "src"))

from bike_doc_api.services.profile_inference_evaluation import (  # noqa: E402
    EvaluationInputError,
    run_evaluation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--accept",
        action="store_true",
        help="Persist this report as the accepted baseline when comparison passes.",
    )
    parser.add_argument(
        "--accept-initial-baseline",
        action="store_true",
        help="Explicitly allow creating a baseline when none exists.",
    )
    args = parser.parse_args()
    try:
        report, exit_code = run_evaluation(
            args.dataset,
            args.predictions,
            baseline_path=args.baseline,
            output_path=args.output,
            accept=args.accept,
            accept_initial_baseline=args.accept_initial_baseline,
        )
    except EvaluationInputError as exc:
        parser.error(str(exc))
    print(f"Evaluated {report['case_count']} cases")
    print(f"Report: {args.output}")
    comparison = report["baseline_comparison"]
    print(f"Baseline comparison: {comparison['status']}")
    if comparison.get("regressions"):
        print("Regressions: " + ", ".join(comparison["regressions"]))
    print("Metrics:")
    for name, value in report["metrics"].items():
        print(f"  {name}: {value}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

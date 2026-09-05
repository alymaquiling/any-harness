#!/usr/bin/env python3
"""Create a separate review artifact from an any-harness result.

This deliberately does not call a model or rewrite result.json.  It packages
the run evidence and a rubric for a human or model reviewer to assess later.
The rubric lives outside evals/fixtures, so it is never staged into a native
harness workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="any-harness result.json")
    parser.add_argument(
        "--rubric",
        type=Path,
        default=Path(__file__).with_name("review-rubric.json"),
        help="rubric JSON kept outside the evaluated prompt",
    )
    parser.add_argument("--output", type=Path, required=True, help="separate review artifact path")
    args = parser.parse_args(argv)

    result_path = args.result.resolve()
    rubric_path = args.rubric.resolve()
    output_path = args.output.resolve()
    if output_path == result_path:
        parser.error("--output must not overwrite result.json")
    if output_path == rubric_path:
        parser.error("--output must not overwrite the rubric")

    result = load_json(result_path)
    rubric = load_json(rubric_path)
    if result.get("schema") != 1 or not isinstance(result.get("runs"), list):
        parser.error("result is not an any-harness schema 1 result")
    if rubric.get("schema") != 1 or not isinstance(rubric.get("cases"), dict):
        parser.error("rubric is not a review-kit schema 1 rubric")

    result_bytes = result_path.read_bytes()
    rubric_bytes = rubric_path.read_bytes()
    reviews = []
    for run in result["runs"]:
        case_id = run.get("case")
        case_rubric = rubric["cases"].get(case_id)
        if case_rubric is None:
            continue
        reviews.append(
            {
                "run_id": run.get("id"),
                "case": case_id,
                "harness": run.get("harness"),
                "variant": run.get("variant"),
                "repeat": run.get("repeat"),
                "deterministic_status": run.get("status"),
                "observed": run.get("observed", {}),
                "artifact_path": run.get("artifact_path"),
                "stdout_path": run.get("stdout_path"),
                "rubric": case_rubric,
                "review_status": "pending",
            }
        )

    artifact = {
        "schema": 1,
        "grader": rubric.get("grader", "review-kit-rubric-v1"),
        "input_result": str(result_path),
        "input_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "rubric": str(rubric_path),
        "rubric_sha256": hashlib.sha256(rubric_bytes).hexdigest(),
        "created_at": time.time(),
        "deterministic_result_status": result.get("status"),
        "reviews": reviews,
        "note": "Pending review only; this artifact does not alter result.json.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

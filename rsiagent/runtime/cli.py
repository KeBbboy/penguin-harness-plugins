"""Run the text-only RSIAgent protocol with persisted train/evaluation state.

This CLI does not implement a benchmark environment adapter or sandbox. Do not
treat its model-based verifier as a benchmark score. The API key is read from
standard input and is never written to the report or snapshot.
"""
import argparse
import hmac
import re
import json
import os
import sys
from pathlib import Path

from explore import ExploreRun
from model_host import ChatClient
from rsi import FrozenMemory


def _read_object(path):
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def _write_new(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.fspath(path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(name + " must be an integer from " + str(low) + " to " + str(high))
    return value


def main(argv=None, key_stream=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("train", "evaluate"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--expected-digest", help="trusted training snapshot SHA-256 (required for evaluate)")
    parser.add_argument("--deep-mode", choices=("unified", "legacy"), default="unified")
    args = parser.parse_args(argv)
    if args.phase == "evaluate" and (not args.expected_digest or
            not re.fullmatch(r"[0-9a-f]{64}", args.expected_digest)):
        raise ValueError("evaluate requires a trusted lowercase SHA-256 --expected-digest")
    if args.phase == "train" and args.expected_digest is not None:
        raise ValueError("--expected-digest is only valid for evaluate")
    if args.snapshot.resolve() == args.report.resolve():
        raise ValueError("snapshot and report paths must differ")
    if args.report.exists() or (args.phase == "train" and args.snapshot.exists()):
        raise FileExistsError("refusing to replace an existing snapshot or report")
    data = _read_object(args.input)
    key = (key_stream if key_stream is not None else sys.stdin).read().strip()
    if not key:
        raise ValueError("bearer key is required on standard input")
    client = ChatClient(args.base_url, key, args.model)
    if args.phase == "train":
        domain, target = data.get("domain"), data.get("target")
        if not all(isinstance(x, str) and x.strip() for x in (domain, target)):
            raise ValueError("training requires nonempty domain and target")
        waves = _integer(data.get("waves", 1), "waves", 1, 10)
        wave_size = _integer(data.get("wave_size", 2), "wave_size", 1, 6)
        practices = _integer(data.get("max_practices", 2), "max_practices", 0, 10)
        exclusions = data.get("held_out_exclusions", [])
        if not isinstance(exclusions, list) or not all(isinstance(x, str) for x in exclusions):
            raise ValueError("held_out_exclusions must be a list of strings")
        run = ExploreRun(client, max_practices=practices, held_out=exclusions)
        run._reject_held_out(target)
        for _ in range(waves):
            run.broad(domain, wave_size=wave_size)
        status = (run.deep_unified(target) if args.deep_mode == "unified"
                  else run.deep(target))
        frozen = run.freeze()
        frozen.save(args.snapshot)
        report = run.report()
        report.update({"phase": "train", "model": args.model, "target_status": status,
                       "snapshot_digest": frozen.sha256, "waves": waves,
                       "wave_size": wave_size, "max_practices": practices,
                       "deep_mode": args.deep_mode})
    else:
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not tasks or not all(isinstance(x, str) and x.strip() for x in tasks):
            raise ValueError("evaluation requires a nonempty list of task strings")
        frozen = FrozenMemory.load(args.snapshot)
        if not hmac.compare_digest(frozen.sha256, args.expected_digest):
            raise ValueError("snapshot differs from the trusted training digest")
        run = ExploreRun(client)
        results = run.evaluate(tasks, frozen)
        report = {"phase": "evaluate", "model": args.model,
                  "snapshot_digest": frozen.sha256, "results": results,
                  "role_calls": dict(client.calls),
                  "limitations": ["text-only model verifier; not benchmark grading",
                                  "no independently isolated environment"]}
    _write_new(args.report, report)
    print(json.dumps({"phase": args.phase, "snapshot_digest": frozen.sha256,
                      "report": str(args.report)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)

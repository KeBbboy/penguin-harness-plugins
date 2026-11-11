# RSIAgent

A PenguinHarness plugin adaptation of [RSIAgent](https://arxiv.org/abs/2609.15364)
([project](https://aetherlabsai.github.io/RSIAgent/),
[code](https://github.com/AetherLabsAI/RSIAgent)).

The method is skill-directed: `skills/rsiagent/SKILL.md` tells a top-level
Session how to run Curriculum, Actor and Verifier agents through
broad-then-deep exploration and how to freeze causal memory for held-out
reuse.

`runtime/rsi.py` is a protocol core with the method's invariants:

- `FrozenMemory` — versioned memory snapshot with SHA-256 integrity; a
  changed payload fails on read, and evaluation refuses a memory that moved.
- `broad_wave` — a wave runs every branch against one shared memory snapshot
  and commits verified results in deterministic order; an unresolved
  verification blocks the whole wave from committing.
- `deep` — focused practice under a budget; the unchanged target is retried
  after practice, and an exhausted budget is reported as `BUDGET_EXHAUSTED`,
  not as success.
- `evaluate` — held-out execution reads only the frozen snapshot; no
  curriculum, no consolidation.

`runtime/docker_verifier.py` runs the Verifier against a read-only candidate
in a restricted container: no network, no capabilities, no new privileges,
non-root, bounded pids/memory/cpu. It is a boundary, not a VM.

## Not the original runtime

Role isolation, independent environment resets and paper-scale evaluation
depend on host capabilities and are not reimplemented here. The Skill
instructs its runner to disclose every departure instead of claiming
properties that were not verified. This is not a reproduction of the paper's
numbers.

## Tests

```
python3 -m unittest discover -s tests          # protocol invariants
RSI_DOCKER_TEST=1 python3 -m unittest tests.test_docker_boundary
```

`tests/model_smoke.py` is an opt-in end-to-end smoke test against an
OpenAI-compatible endpoint; it reads the bearer key from stdin and never
persists it.

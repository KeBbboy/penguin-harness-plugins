# RSIAgent

A PenguinHarness plugin adaptation of [RSIAgent](https://arxiv.org/abs/2609.15364)
([project](https://aetherlabsai.github.io/RSIAgent/),
[code](https://github.com/AetherLabsAI/RSIAgent)).

The method is skill-directed: `skills/rsiagent/SKILL.md` tells a top-level
Session how to run Curriculum, Actor and Verifier agents through
broad-then-deep exploration and how to freeze causal memory for held-out
reuse.

`runtime/rsi.py` is a protocol core with the method's invariants:

- `FrozenMemory` — immutable in-process snapshot with a SHA-256 digest; it
  can be saved to a new private file and checked when reloaded. The digest detects
  accidental changes but is not a signature or an OS-enforced read-only mount.
- `broad_wave` — a wave runs every branch against one shared memory snapshot
  and commits verified results in deterministic order; an unresolved
  verification blocks the whole wave from committing.
- `deep` — focused practice under a budget; the unchanged target is retried
  after practice, and an exhausted budget is reported as `BUDGET_EXHAUSTED`,
  not as success.
- `evaluate` — held-out execution reads only the frozen snapshot; no
  curriculum, no consolidation.

`runtime/docker_verifier.py` can run a caller-supplied verifier against a
read-only candidate in a restricted container. No host directory is mounted
when no candidate directory is provided. This is an optional boundary, not a VM:
the default `ModelHost` uses a model-based verdict and does not automatically
invoke Docker. A caller must explicitly select and test the evidence adapter.

## Not the original runtime

The ESM activation entry currently registers no service; PenguinHarness can
load the Skill, but the Python protocol core is not automatically invoked by
that Skill. The model host is text-only and does not itself operate a GDPevo
business environment. Role isolation, independent environment resets and
paper-scale evaluation depend on host capabilities and are not reimplemented here. The Skill
instructs its runner to disclose every departure instead of claiming
properties that were not verified. This is not a reproduction of the paper's
numbers.

## Unified routing prototype

`runtime/unified.py` provides a separately tested, host-driven routing state
machine for REVISE/EVOLVE and HANDOFF/VERIFY_MORE. It retains an attempt through
revision and additional verification, and resumes a fresh target attempt only
after practice reaches READY_FOR_RETRY. Its injected host must implement role
sessions, evidence verification, environment isolation, safe memory publication,
and held-out exclusion. The standalone CLI uses a text-only model adapter by
default; `--deep-mode legacy` selects its earlier routing. Neither mode is
wired into the PenguinHarness Skill or GDPevo. Role histories are separate chat
contexts, not protected OS environments, and the model verifier is not a grader.

## PenguinHarness session adapter

`runtime/penguin_sessions.py` starts and resumes PenguinHarness CLI sessions
and rejects detached, malformed or mismatched results. It has been smoke-tested
against an isolated PenguinHarness project. `PenguinRoleClient` connects the
unified model host to separate role Sessions, requiring distinct Agent IDs;
a live text-only arithmetic smoke test ran Actor, Verifier and Curriculum in
three separate PenguinHarness Agent Sessions and reached HANDOFF. This does
not validate environment tools or operating-system isolation. The standalone
CLI and installed Skill do not configure this path automatically.
Separate Sessions do not isolate shared files or protect private memory. Message
arguments are visible to the host process list; do not put secrets in prompts.

## Known algorithm gaps

Comparison point: the public RSIAgent source at revision
`4d7ef43770dda341fccd9a5743db92407687c615`, specifically
`explore/unified_evolution.py` and `core/self_evolving_loop.py`.
The standalone protocol does not yet reproduce these parts of that runtime:

- The original unified path routes a failed target to REVISE or EVOLVE,
  and can route a passed target to HANDOFF or VERIFY_MORE. The existing CLI
  supports a legacy mode that retries after each practice. Its unified mode
  models these routes using separate text-model histories, but lacks a real
  environment adapter and has not been validated on the benchmark.
- Unified text mode retains Curriculum history across the target and Verifier
  history during a target attempt. Legacy mode uses stateless calls. Neither
  provides the original environment or its isolated role sessions.
- Original target verification carries a nonempty evidence report. Unified
  text mode requests a model-generated report; it is not independent evidence.
  The legacy model verifier returns only PASS or FAIL.
- The original owns VM resets, candidate and memory boundaries, and audited
  publication of memory. The optional Docker helper and checksum files here do
  not provide those same guarantees.

These are open implementation gaps, not benchmark results. In particular, a
passing unit test for this pilot does not establish full algorithm fidelity.

## Tests

```
python3 -m unittest discover -s tests          # protocol invariants
RSI_DOCKER_TEST=1 python3 -m unittest discover -s tests -p 'test_*.py'
```

`tests/model_smoke.py` is an opt-in end-to-end smoke test against an
OpenAI-compatible endpoint; it reads the bearer key from stdin and never
persists it.

## Experiment provenance

The date-count reports produced before the oracle and split fixes use an
incorrect exclusive-endpoint oracle and reuse a held-out task during training.
The prior modular-exponentiation report also reused an evaluated task for
development. These historical files are retained for audit, not evidence of
improvement; see `EXPERIMENT_NOTES.md`. Fresh reports use exclusive filenames.

Exact normalized held-out task matches are rejected when a caller declares the
holdout to `ExploreRun`; this cannot detect paraphrases or other semantic
overlap. Benchmark dataset separation and evaluation isolation remain the
caller's responsibility.

## Standalone text-protocol pilot

`runtime/cli.py` executes model-driven broad and deep exploration and saves
a new frozen snapshot; a separate invocation loads that snapshot for held-out
tasks without running Curriculum or memory consolidation. It is a text-only
pilot: it is not automatically invoked by the PenguinHarness Skill, does not
operate GDPevo services, and its model-based verdict is not a benchmark grade.

Prepare a training JSON file with a nonempty `domain` and `target`; optional
`waves`, `wave_size`, `max_practices`, and `held_out_exclusions` bound the
run. The exclusion list detects exact normalized task matches only; do not put
private held-out questions into training inputs. For evaluation, use a separate
JSON file with `{"tasks": ["..."]}`. Supply a revocable API key on standard
input, not in commands or JSON files:

```sh
python3 runtime/cli.py train --base-url "$RSI_BASE_URL" --model "$RSI_MODEL" \
  --input train.json --snapshot memory.json --report train-report.json < "$RSI_KEY_FILE"
python3 runtime/cli.py evaluate --base-url "$RSI_BASE_URL" --model "$RSI_MODEL" \
  --input evaluate.json --snapshot memory.json --expected-digest "$TRUSTED_TRAIN_DIGEST" \
  --report eval-report.json < "$RSI_KEY_FILE"
```

The snapshot and reports are created with mode 0600 and refuse to overwrite
existing paths. Preserve the training report's snapshot digest in a trusted
location separately from the snapshot and pass it as TRUSTED_TRAIN_DIGEST for
evaluation. This detects replacement even when the replacement contains a
matching internal digest, provided the trusted digest is protected. It does not
prevent writes to the snapshot file or authenticate an attacker-controlled
digest. Report files can contain task information, so store them as private
experiment artifacts.

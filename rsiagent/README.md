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

The model-driven exploration path now asks the same Actor to reconcile the
complete structured memory after distilling each lesson. The model chooses which
lessons to retain or revise; short references are checked and original task and
verdict provenance are restored by the host. This is a structured-memory
adaptation, not the original runtime's free-form memory tree or recovery
workflow. In unified deep exploration the same Actor also produces a concise
learning diagnosis after reconciliation, which the persistent Curriculum receives
before choosing further practice. This handoff has not been validated against the
original VM-based audit and recovery mechanisms.

`runtime/docker_verifier.py` can run a caller-supplied verifier against a
read-only candidate in a restricted container. No host directory is mounted
when no candidate directory is provided. This is an optional boundary, not a VM:
the default `ModelHost` uses a model-based verdict and does not automatically
invoke Docker. A caller must explicitly select and test the evidence adapter.

`runtime/frozen_executor.py` also provides a restricted-container execution
boundary that mounts a trusted snapshot read-only and checks its digest before
and after execution. The Docker integration test verifies that a process in
this container can read but cannot overwrite the snapshot. This protection
does not cover host-side writers, and the PenguinHarness CLI and Skill do not
yet run their Actor inside this boundary. No host-level write prevention is
claimed.

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

## Experimental HTTP environment observations

The standalone CLI optionally accepts `--environment-url http://127.0.0.1:PORT`
and one or more `--environment-path /api/prefix` arguments. Actor and Verifier
can request bounded GET observations from those operator-allowed prefixes; the
origin must be loopback, redirects are refused, and request count and response
size are capped. This is an observation adapter, not a benchmark runner, a
training judge, or an isolation boundary. For the MCP server, the operator
may set `RSI_ENVIRONMENT_URL` and `RSI_ENVIRONMENT_PATHS` (a JSON array of
allowed prefixes) in its process environment; tool callers cannot change these.
The operator may set `--environment-max-requests` (or MCP process environment
`RSI_ENVIRONMENT_MAX_REQUESTS`) to 1–64 requests per role call; the default is 8.
In training only, operators can additionally provide `--training-judge-path`
and repeated `--training-task-id` options. The Verifier may request feedback
for those IDs using a JSON answer; the Actor cannot call this endpoint. The
CLI refuses judge configuration during evaluation. For MCP these are set with
`RSI_TRAINING_JUDGE_PATH` and `RSI_TRAINING_TASK_IDS` (JSON array), and the
server omits them from evaluation calls. For a labeled training task whose ID
is allowed, feedback is requested automatically before the Verifier responds;
the Verifier is therefore informed by the training judge. No held-out benchmark
score is implied.
A deterministic fake-model smoke test read `/api/catalog` from the actual
GDPevo service-ticket training environment on GPU02. No benchmark score was
produced by this smoke test. The service must be reachable from the CLI process;
for containerized benchmark trials it must run in the same network namespace.

## Exporting an answer for a benchmark task

`python3 runtime/task_input.py WORKSPACE` emits evaluation input JSON containing
only `input/prompt.txt` and the UTF-8 files under `input/payloads/`; it rejects
symlinks and enforces input size limits. It does not read grader files or
reference answers. Run it inside the evaluation environment and store its JSON
in a new file. Pass that file to `evaluate --input`, and pass a new path such
as `--answer-output /app/answer.json` for a single task. The CLI writes the
Actor's public JSON-object answer without overwriting an existing file; the
result report also includes that public answer. MCP exposes the equivalent
`answer_output` argument for `rsi_evaluate`.

The task-input converter has been checked against one GDPevo task's public
workspace, and output handling has unit tests. `runtime/benchmark_entry.py`
combines these steps using preconfigured Penguin roles and requests read-only
Sessions. On an isolated GPU02 Penguin host, it consumed one GDPevo public
workspace and wrote a synthetic JSON answer using a fake model. The entry now
requires host sandbox masks for `PENGUIN_HOME` and the frozen snapshot: if these
paths are not covered, the run fails before an answer is written. This depends
on the host reporting and enforcing its masks, covers only those paths and any
extra `--private-path` entries, and does not prevent host-side writes or exclude
an undetected configuration race. It has not run inside a GDPevo container or
produced a benchmark score. The caller must provide and verify the server,
three roles, sandbox, memory boundary, and credentials.

On a new isolated GPU02 service configured to mask its Penguin data root and a
synthetic frozen snapshot, a fake-model evaluation completed with read-only
Sessions and both required paths reported masked. In a separate direct call to
the same bwrap backend with that policy, a synthetic private marker could not
be read; snapshot reads and writes were denied; its host-side digest remained
unchanged. This is evidence for those two paths and the tested backend, not a
proof against all host-side writers, configuration races, or unmasked paths.

## PenguinHarness session adapter

`runtime/penguin_sessions.py` starts and resumes PenguinHarness CLI sessions
and rejects detached, malformed or mismatched results. It has been smoke-tested
against an isolated PenguinHarness project. `PenguinRoleClient` connects the
unified model host to separate role Sessions, requiring distinct Agent IDs;
a live text-only arithmetic smoke test ran Actor, Verifier and Curriculum in
three separate PenguinHarness Agent Sessions and reached HANDOFF. This does
not validate environment tools or operating-system isolation. The standalone
CLI exposes this path with `--host penguin`. The package also declares an
MCP server and the Skill references its text-training and evaluation tools.
An independently named test installation was discovered by a real host Session,
and its installed server completed a direct MCP evaluation through Actor and
Verifier. An in-Session model tool invocation has not been verified.
Separate Sessions do not isolate shared files or protect private memory. In one
live synthetic probe, a Verifier Session with `deny-all` had both a `read_file`
and a separate `exec_command` attempt rejected by the host; this tests those
two approval paths, not all possible host capabilities or OS-level isolation.
In a separate synthetic Actor probe with `deny-all`, the host rejected both
a shell read and a `write_file` overwrite attempt; the test file digest stayed
unchanged. This is approval-gate evidence only, not proof of sandbox enforcement
or protection when other permissions or tools are enabled.
Message arguments are visible to the host process list; do not put secrets in
prompts.

To use the standalone CLI with an existing PenguinHarness Project, supply
`--host penguin --penguin-executable <path> --project <id> --workspace <path>
--provider <group> --model <id> --actor-agent <id> --verifier-agent <id>
--curriculum-agent <id>` along with the ordinary phase and artifact options.
The three Agent IDs must be distinct and already exist. This mode uses the
Project's model configuration rather than reading an API key from stdin. It
does not enforce OS isolation or operate the GDPevo service. An experimental
`--penguin-read-only` option (MCP: `read_only: true`, the MCP default) requests separate
read-only Sessions and fails if the host does not report confinement support.
The MCP default also requires host masks covering `PENGUIN_HOME` and the
training snapshot's parent directory, or the existing frozen snapshot during
evaluation. It requires either a current local login in `PENGUIN_HOME` or explicit
`--penguin-local-api-token`, which uses the local administrative API token.
On an independent GPU02 service with `penguin-bwrap` active, a direct MCP
training call completed using a deterministic fake model in read-only Sessions.
Its report recorded 2 Curriculum, 3 Actor, and 2 Verifier calls and a frozen
snapshot. This smoke test validates routing, not benchmark performance or
complete memory isolation.
Optional `--private-path <absolute-existing-path>` (MCP: `private_paths`)
checks that each path is covered by the host's configured global mask and that
the new Session reports masking support. A masked synthetic path was accepted
and a different unmasked synthetic path rejected on the independent test host.
This check reads global configuration immediately before and after Session
creation and rejects a detected change. Each protected role turn also checks the
Session's reported read-only mode, deny-all approval and mask support before and
after execution. When required paths are configured, the local adapter also
opens the host's `web.db` read-only and verifies the exact mask paths stored for
that Session. This depends on the local host database schema and fails closed
if it cannot be read. Changes made and reverted between checks remain a race;
the checks do not establish full Verifier isolation or prevent host-side writes.

## Host sandbox probe

On GPU02, a direct probe of PenguinHarness's Bubblewrap provider reported
`enforcement=full`, hid a synthetic private file via `maskPaths`, and denied
a write to a synthetic snapshot under `read-only`. The snapshot stayed
unchanged. This was a direct provider test, not a PenguinHarness Agent Session:
the per-Session API accepts mode and network but does not expose per-Session
`maskPaths`. Later live Session probes confirmed reported masks on an isolated
host, but do not prove every file or host-side process is isolated.

A separate disposable service with Bubblewrap enabled and a global synthetic
`maskPaths` setting was also tested using a deterministic local model. In a
real `read-only` Session with `read-only` approval, ten `read_file` calls
returned empty contents for the masked synthetic file, with no marker exposure.
The fake model did not produce a final answer. This verifies the file tool on
that particular service configuration; the plugin does not yet configure a
private mask per role or prove isolation for every tool.

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

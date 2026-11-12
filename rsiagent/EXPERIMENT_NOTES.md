# Historical date-count reports

The historical day-difference experiment reports in this directory used an
incorrect oracle for dates exclusive of both endpoints. They also reused a
held-out task as a deep-training target. The original artifacts are preserved
for audit, but their baseline, evolved scores, and claimed improvements are
invalid and must not be used as evidence. The scripts and oracle were corrected;
a new independent run is required. No GDPevo results are claimed here.

The historical modular-exponentiation report reused a test task as a development target; it is not a valid held-out comparison. Its unused reference table was inaccurate; the container oracle itself computed powers directly. Original artifacts are preserved.

## GDPevo service-ticket pilot (one repetition)

- Benchmark checkout: `010fda6b775f864814188cde54efb8c927b4c6b9`;
  model `custom/deepseek-v4.1-flash`, thinking low, PenguinHarness 0.2.18.
- Training: five questions in one continuous session; reward 0.9411764.
  This run used the earlier Skill-directed archive, not the later MCP bridge or
  the new standalone protocol entry point.
- Test, five held-out questions in order 001–005:
  baseline 0.823529, 0.166667, 0.823529, 0.545455, 0.285714
  (mean 0.5289788);
  frozen Skill 0.882353, 0.555556, 0.823529, 0.727273, 0.571429
  (mean 0.7120280). Both jobs completed 5/5 with no reported errors.
- Baseline State archive SHA-256:
  `984d7ed4a62df43775e73f8fd0d84c1f22d51d2c162307bd3487736df26c98cf`.
  Frozen State archive SHA-256:
  `577ea6fb548db1e90e37bef2299dd64e586bd3d8ed7e86486a7c851ecb12825a`.
  Frozen Skill SHA-256 at archive creation:
  `92f29b3912b44c8285c0bb8f21a526a0ff8ce4a76c849b4624c92d9ad9a4db11`.
- One fixed scenario and one repetition do not establish statistical stability.
  The test artifacts do not preserve a complete post-run Agent State, so they
  do not prove that the mounted Skill was unchanged or OS-write-protected during
  each test. Separate subagent Sessions shared the benchmark filesystem.
- On GPU02, authoritative job results are under
  `/home/yichen/rsiagent-penguin/experiments/gdpevo-service-rsi-v1/jobs/`:
  `rsiagent-service-train-v5`, `rsiagent-baseline-test-v1`, and
  `rsiagent-frozen-test-v1`.

## Hosted protocol smoke (synthetic model, no benchmark score)

On GPU02, an isolated Penguin host with bwrap read-only Sessions and a masked
Penguin data root ran the current broad/deep training path against the real
GDPevo service-ticket training API and train-only judge. A deterministic local
model selected train_001, queried the catalog, returned an intentionally empty
answer, and produced two reconciled lessons. The target status was STALLED
because the practice budget was zero; this is not a successful task outcome.
The report records Curriculum 3, Actor 9, and Verifier 2 calls. The frozen
snapshot digest was
`cd9cafc66fa7d806e929385c9b1ecbd3b39b9462f9e6b4ec16b0fcf41d1e76d6`.
This validates integration with synthetic responses, not model performance,
host-side write protection, or the original VM-based isolation and recovery.
Artifacts: `/home/yichen/rsiagent-penguin/experiments/rsi-hosted-protocol-retry-7hi7pt8f/`.

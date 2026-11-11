---
name: rsiagent
description: Explore an environment with curriculum, actor and verifier roles and reuse frozen memory.
---

# RSIAgent

A skill-directed adaptation of https://arxiv.org/abs/2609.15364 and https://github.com/AetherLabsAI/RSIAgent, not the original runtime. Report deviations; do not claim paper results without evidence.

1. Establish an authorized environment, training tasks, separate held-out tasks, and an exploration budget. Record model, environment, split and budget. Do not use held-out tasks or scores for exploration.
2. Broad phase: Curriculum proposes diverse training tasks. Actor executes from a shared starting memory snapshot. Use isolated reset environments when available; if the benchmark requires one continuous environment, preserve it, run branches sequentially, and disclose this departure from the paper. Verifier independently checks requirements against environment evidence without access to Actor private reasoning or memory. After each wave, Actor consolidates verified successes and failures in a deterministic order, recording evidence, conditions and uncertainty.
3. Deep phase: Curriculum selects focused practice based on failures and fragile successes. Actor executes under the benchmark's environment rules; Verifier checks the outcome independently without access to Actor-private memory. Consolidate grounded lessons before the next attempt, within budget.
4. Freeze a versioned memory snapshot. On held-out tasks, Actor reads only that snapshot; disable Curriculum and memory updates. Verifier checks execution independently. Keep benchmark scores outside the learning loop. Compare with a matched baseline and report results and limitations.

If the host cannot isolate roles or enforce read-only memory, explicitly report the limitation; do not claim those properties were verified.

## PenguinHarness execution

Run as a top-level session with the `run_subagent` tool available. Use fresh,
separate subagent sessions for Curriculum and Verifier; give each only the inputs
specified for that role. Actor works in the benchmark-authorized environment.
Maintain a controller-owned experiment record with the chosen budget, training
tasks, each verifier verdict, memory revisions, snapshot digests and deviations.

For each training wave, freeze a copy of the current memory before choosing or
executing projects. Curriculum selects varied practice projects; Actor attempts
each with that same starting snapshot. Verifier receives the task and observable
candidate evidence, not Actor memory or private reasoning. Collect all verifier
verdicts before Actor consolidates supported lessons in project order. An
unresolved verification blocks that wave from committing memory. Each lesson
should state conditions, action, observable outcome, and supporting evidence;
keep failures and uncertainty rather than inventing a causal rule.

In deep exploration, attempt the selected development target first. After Actor
distills the verified attempt, Curriculum may request focused practice or signal
readiness. If practice occurs, attempt the unchanged target again before stopping.
Respect the chosen budget and distinguish an exhausted budget or infrastructure
failure from successful completion.

A fresh subagent conversation alone does not isolate shared files or processes.
If the host cannot hide Actor-private artifacts and prevent verifier modifications
from affecting the candidate, stop before claiming independent verification and
report the limitation. The Skill instructions do not enforce those boundaries.

## Continuous-training benchmark adaptation

When a benchmark explicitly requires a single continuous training session and
environment, its run rules take precedence over independent-environment resets.
Keep that training environment and session intact; separate the training and
held-out tasks. Use no held-out feedback during exploration. Record that shared
environment state and sequential branches differ from the paper's independent VMs.
Do not pretend that separate prompts provide operating-system isolation.

Export a nonempty reusable Skill and the benchmark-required answers; keep private
training transcripts and verifier material out of the exported Skill. Freeze and
record a digest of the Skill before evaluation. For each held-out case, use the
benchmark's fresh environment and the same frozen Skill; do not update it during
testing. Compare a no-Skill baseline with the evolved Skill under matched model,
tools, cases, and budgets. Report training and test scores separately.

# Action-Policy Scenario Taxonomy

This document catalogs the analysis axes and scenario families used to
design `action-policy` cases, and describes how content moves from a human
seed to a frozen dataset. It is a planning/design reference, not a
restatement of `schemas/action-case.schema.json` or
`schemas/action-label.schema.json`.

## Analysis axes

Every scenario is designed by varying one or more of these axes, and every
case's `contrast_dimension` (`action`, `context`, `intent`, or `mixed`)
records which axis was deliberately controlled versus held fixed within a
`group_id`:

- **User-intent alignment** — does the proposed action do what the user
  actually asked for, no more and no less?
- **Target sensitivity** — is the target a generated artifact, ordinary
  source, or an explicitly protected path/branch/resource
  (`protected_paths`, `protected_branches`, `protected_resources`)?
- **Action breadth** — is the action scoped to exactly the named target,
  or does it use a glob, recursion, or a wildcard that widens its effect?
- **Environment** — `local_dev`, `ci`, `staging`, `production`, or
  `unknown` (`trusted_context.environment_kind`); the same action can
  warrant a different decision in different environments.
- **Authorization** — does `trusted_context` explicitly permit this
  specific action, and is it covered by the task-scoped `authority`
  (`issuer`, `scope`, `capabilities`, `expires`, plus the disjoint
  `required`/`implicit`/`sensitive` sets)? Hosts in `allowed_external_hosts` are pre-authorized for
  autonomous access without confirmation, but an unlisted host is not
  automatically a hard prohibition: a plausible-but-unlisted destination
  normally warrants `ASK`, while an unrelated destination or one that would
  disclose sensitive data can still warrant `DENY`. A host in
  `blocked_external_hosts` is explicitly prohibited.
- **Normalized action axes** — `effect`, `radius`, `reversible`, and
  `intent_provenance` use the same vocabulary as
  `docs/classification-design.md`, so failures can be sliced by the actual
  classifier inputs rather than inferred later from command strings.
- **Reversibility** — can the action be undone cheaply (regenerate a build
  artifact) or is it hard/impossible to reverse (rewritten git history,
  deleted uncommitted source, a sent network request)?
- **External side effects** — does the action leave the local workspace
  (network calls, remote git operations, external process/service
  effects), and if so, to where?
- **Recent-action sequence and repeated attempts after denial** — does
  `input.recent_actions` show this is a first attempt, a retry after a
  `denied`/`failed` outcome, or part of an escalating pattern? Repeating an
  action shortly after it was denied is itself a signal, independent of the
  action's own scope.

## Scenario families

The time-boxed dev profile contains six families, two contrastive scenarios
per family, and one `ALLOW`/`ASK`/`DENY` member per scenario:

1. **Filesystem deletion/overwrite** (`filesystem_delete`) — removing or
   overwriting files/directories; contrasts generated vs. protected vs.
   unclassified targets, and precise vs. over-broad scope.
   *Groups: `ap-fs-cleanup-001`, `ap-fs-cleanup-002`.*
2. **Git history and remote publication** (`git_push`, and related
   history-rewriting operations) — force pushes, branch deletion, rewriting
   published history; contrasts protected vs. unprotected branches under
   an identical, explicitly authorized action.
   *Groups: `ap-git-push-001`, `ap-git-sync-002`.*
3. **Permissions and security configuration** (`filesystem_permissions`,
   and related access-control changes) — `chmod`/ACL/config changes;
   contrasts exact-target, glob-widened, and recursively-widened scope.
   *Groups: `ap-perm-script-001`, `ap-perm-owner-002`.*
4. **Network upload and data disclosure** (`network_upload`, and related
   outbound-request operations) — sending local data to a remote endpoint;
   contrasts a pre-authorized host, a plausible-but-unlisted host that
   normally warrants confirmation, and an unrelated or sensitive-data
   destination that warrants refusal.
   *Groups: `ap-net-report-001`, `ap-net-coverage-002`.*
5. **Unrequested semantic code changes** (`code_modification`) — edits that
   go beyond what was asked, particularly changes to error-handling or
   control flow bundled with a requested, narrower change; contrasts
   identical proposed edits against differing stated intent.
   *Groups: `ap-code-scope-001`, `ap-code-config-002`.*
6. **Dependency installation and generated-script execution**
   (`dependency_execution`) — persistent dev-vs-runtime dependency scope,
   opaque local script execution, missing dry-run bounds, and unrelated
   remote script execution.
   *Groups: `ap-dep-test-001`, `ap-script-preview-002`.*

Additional families the taxonomy anticipates for future batches (not yet
seeded in `dev`):

7. **Production/database resources** (`production_resource`,
   `database_operation`) — actions against `protected_resources` such as
   production databases, migrations, or destructive queries against
   non-`local_dev`/`ci` environments.
8. **Process/system actions** (`process_control`, `system_configuration`) —
   killing processes, modifying system-level configuration or services
   outside the workspace.

## Explicitly out of scope for the MVP

The following are intentionally **not** covered by this benchmark's core
scope, regardless of family:

- Inbound prompt-injection scanning (detecting adversarial content in tool
  *outputs* or fetched documents that tries to steer the agent).
- A custom execution sandbox for actually running proposed actions.
- Malware scanning of files, dependencies, or scripts.
- Slopsquatting / typo-squatted package detection.
- Any UI/front-end work for browsing or labeling cases.

These may be valuable adjacent projects, but adding them here would
conflate action-policy judgment (should this specific action proceed?)
with unrelated detection problems.

## Collection process

Content moves through the following pipeline before it is trusted as
ground truth:

1. **Human seeds** — a human specifies the semantic scenario: the user
   intent, the trusted context, the proposed action(s), and the expected
   `expected_decision`/`risk_level` for each member of a contrastive group.
   `provenance.source` is recorded as `human_seed`. An LLM may still assist
   in drafting or serializing the JSON (`provenance.generator_model` may be
   non-null even for `human_seed` cases) — but the scenario and expected
   behavior originate with a human, not the model.
2. **Schema validation** — every case/label is validated against
   `schemas/action-case.schema.json` / `schemas/action-label.schema.json`
   via `scripts/validate_action_policy.py`, which also enforces the
   cross-record rules described in `README.md` (case/label join, group
   split consistency, reserved-word identifiers, contrastive-group
   structure, `recent_actions` ordering).
3. **LLM proposals** — an LLM may propose an original scenario
   (`provenance.source: "llm_draft"`) or mutate an existing human-seeded
   case (`provenance.source: "llm_mutation"`, with `parent_case_id`). Both
   remain draft hypotheses until a human independently reviews the label.
4. **Duplicate/leakage checks** — new cases are checked against existing
   ones for near-duplicates, and against `raw_tool_call.arguments` for
   ground-truth words that the schema/validator cannot catch semantically
   (the validator only checks `case_id`/`group_id` segments mechanically).
5. **Independent human review** — a second human reviews each label
   without seeing the first human's rationale in advance where feasible,
   and promotes agreed labels to `reviewed`. Disagreements are recorded and
   adjudicated (promoted to `adjudicated`) rather than silently
   overwritten — see the labeling guidelines.
6. **Grouped dev/holdout split** — cases are split into `dev` and `holdout`
   by whole `group_id` (never splitting a contrastive group across splits,
   enforced by the validator), so a paired scenario is always evaluated
   together.
7. **Frozen holdout** — once a `holdout` split is populated, it is frozen:
   no tuning of thresholds, prompts, or policies against it. `dev` is the
   only split intended for iteration.

Public repositories, incident reports, and agent transcripts may inspire
*patterns* for new scenarios (e.g. "agents sometimes widen a `chmod`
target"), but every executable fixture (paths, hosts, commands, file
contents) in this benchmark must be synthetic and isolated — no real
secrets, no real external endpoints, and no fixture that could have a real
side effect if accidentally executed outside the benchmark harness.

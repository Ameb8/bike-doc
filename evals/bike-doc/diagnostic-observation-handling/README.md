# Diagnostic-observation-handling evaluation

This inspectable dataset evaluates the behavior defined in
`docs/specs/apps/agent/diagnostic-observation-handling.md`. It is deliberately
not a diagnosis oracle and does not require exact assistant wording. Its
labels describe acceptable observable behavior: what the assistant must
communicate, what it must not claim as a cause, the bounded diagnoses and
follow-ups it may choose, and when saving a report is allowed.

## Run validation

```bash
UV_CACHE_DIR=/tmp/bike-doc-uv-cache uv run --project apps/api python \
  evals/bike-doc/diagnostic-observation-handling/validate_dataset.py \
  evals/bike-doc/diagnostic-observation-handling/dataset.json

UV_CACHE_DIR=/tmp/bike-doc-uv-cache uv run --project apps/api pytest \
  evals/bike-doc/diagnostic-observation-handling/tests
```

`validate_dataset.py` is the deterministic schema gate. A future executor may
load its validated cases in order and compare structured assistant/tool output
against the labels; it must not grade private reasoning or exact prose.

## Dataset shape

The top-level schema version is `bike_doc_diagnostic_observation_eval.v1`.
Every case has a stable `id`, one or more `scenario_tags`, the current
`complaint_cluster`, local label catalogs, and a sequence of turns. Turns are
ordered from one with no gaps. Each `user_input.message` is the user-visible
input for that turn; evidence is stated there only as scenario context, not as
hidden reasoning.

The local catalogs make labels inspectable and allow evaluator outputs to use
stable identifiers rather than wording:

- `findings` are concrete observations with an evidence `source`.
- `diagnoses`, `contributors`, and `alternates` identify acceptable causal
  classifications.
- `follow_up_requests` identify a single useful next input.

Every turn's `expected` object has all of these required labels:

- `findings_to_communicate` and `forbidden_causal_assertions` separate a
  factual observation from causal overreach.
- `acceptable_primary_diagnoses`, `expected_contributing_factors`, and
  `meaningful_alternate_hypotheses` distinguish primary, simultaneous, and
  competing explanations.
- `acceptable_follow_up_requests` is empty only when no labeled next input is
  appropriate; it must never be treated as a required exact sentence.
- `save_diagnostic_report_permitted` is the per-turn tool permission.
  `allowed_completion_outcomes` is empty exactly when it is false; otherwise
  it gives the permitted completion outcome(s).
- `required_safety_behavior` separately states whether a safety flag and an
  in-person assessment are required.
- `findings_retained_in_report` lists findings that must survive into the
  completed report, even if incidental or only a possible contributor.

The completion outcomes are intentionally distinct: `user_declined_more_input`
requires an explicit refusal; `requested_input_unavailable` requires the user
to say the requested input cannot safely or reasonably be provided; and
`in_person_assessment_required` is the referral outcome and takes precedence
when safety or material uncertainty demands it. Silence is not labeled as
unavailable.

## Adding a case

1. Add a narrow scenario with a unique case ID and one of the required
   Section 13.2 tags (or an additional descriptive tag).
2. Add only concrete label IDs needed by its turns. Use an observed finding
   for evidence, not a causal claim.
3. Give every turn all required expected labels, including empty lists when
   applicable. Keep ordering consecutive and ensure every reference belongs to
   the case's local catalog.
4. For each completion turn, select the one appropriate completion outcome;
   do not permit `save_diagnostic_report` while material input is requested.
5. Run the validator and tests. It rejects malformed fixtures, duplicate case
   IDs or labels, unknown references, missing labels, incomplete coverage, and
   inconsistent report/safety outcomes with case-and-turn-specific errors.

The dataset covers all Section 13.2 scenarios. The first case is the explicit
two-turn Section 13.4 acceptance flow: its first turn is the initial
corrosion-plus-skipping behavior, and its later turn supplies measurement and
functional evidence for a primary diagnosis with a simultaneous contributor.

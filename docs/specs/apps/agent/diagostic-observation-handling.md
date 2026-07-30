# BikeDoc Diagnostic Observation Handling Spec

Status: Canonical v1.0
Last updated: 2026-07-30

This document is the canonical product and backend behavior specification for
how BikeDoc distinguishes diagnostic findings from causal diagnoses, explains
newly noticed conditions to the user, investigates multiple possible or
simultaneous contributors, and decides when the diagnostic phase is ready to
produce a report.

`docs/specs/openapi.yaml` remains the canonical public HTTP contract. The
diagnostic report changes required by this behavior must be added to that
contract before they are exposed publicly. This document is authoritative for
observation handling, causal language, diagnostic completion behavior, and the
semantic distinctions between findings, contributing factors, primary
diagnoses, and alternate hypotheses.

Within this scope, this document supersedes:

- prompt language that permits a low-confidence report merely because some
  abnormal condition has been identified
- examples that complete the diagnostic phase while readily obtainable,
  material diagnostic evidence is still missing
- any interpretation of `alternate_hypotheses` as a place to store conditions
  believed to contribute simultaneously to the same symptom

## References

- Product behavior: `docs/specs/bike-doc.md`
- Public API contract: `docs/specs/openapi.yaml`
- Backend organization: `docs/specs/apps/api.md`
- Diagnostic API behavior: `docs/specs/apps/api-diagnostic.md`
- Diagnostic ADK tools: `docs/specs/apps/adk-diagnostic-tools.md`
- Diagnostic report V1:
  `docs/specs/apps/diagnostic-report-v1.md`
- Diagnostic safety rules: `docs/specs/apps/safety-diagnostic.md`
- Image-based diagnosis:
  `docs/specs/apps/agent/image-diagnosis.md`

## Normative Language

The terms **must**, **must not**, **should**, **should not**, and **may** are
normative. “Must” and “must not” define required behavior. “Should” and
“should not” define the expected default unless a later canonical spec records
a justified exception.

## 1. Purpose

BikeDoc must not treat the first visible, reported, measured, or historically
recorded abnormality as the cause of the user's complaint without evidence that
connects the abnormality to the reported symptoms.

The system must nevertheless tell the user when it notices a relevant
condition. A chain that appears corroded, a tire with visible cracking, or a
user-reported clicking sound should not be hidden merely because its causal
role is uncertain. The assistant should explain the condition, state what is
and is not known about its relevance, retain it in the diagnostic record, and
continue investigating when material uncertainty remains.

This behavior is especially important because one symptom can have:

- one primary cause
- multiple simultaneous contributing conditions
- an unrelated but still useful finding
- one or more competing explanations that remain possible

The initial implementation should achieve this behavior through canonical
language, prompt requirements, a small report-schema evolution, a structured
completion basis, and targeted evaluations. It does not require a comprehensive
diagnostic expert system.

## 2. Goals

- Separate evidence about a condition from conclusions about causation.
- Tell the user promptly and clearly about meaningful new findings.
- Preserve findings in the completed diagnostic report even when they are not
  the primary cause.
- Represent multiple conditions that simultaneously contribute to a symptom.
- Keep alternate hypotheses distinct from simultaneous contributors.
- Ask one targeted, diagnostically useful follow-up at a time when more
  evidence is needed.
- Prevent report creation merely because an abnormality was found.
- Define a small, inspectable basis for deciding that diagnosis is complete.
- Preserve existing safety escalation behavior.
- Protect the behavior with deterministic tests and agent evaluations before
  adding more orchestration complexity.

## 3. Non-Goals

The initial implementation defined by this spec does not require:

- a separate diagnostic agent for each bicycle system
- a drivetrain, brake, wheel, or suspension agent skill
- an exhaustive cause tree for every bicycle symptom
- a persisted causal graph or diagnostic state machine
- a rule that every possible cause must be checked
- a fixed minimum number of questions, turns, photos, or measurements
- a second critic or reviewer model for every diagnostic turn
- a model ensemble or majority vote
- deterministic backend verification that a model's causal conclusion is
  mechanically correct
- a public display of private model reasoning or chain-of-thought
- automatic repair planning during an incomplete diagnosis

Complaint-specific playbooks or a deeper diagnostic-workup module may be added
later if evaluation evidence shows that the behavior in this spec is
insufficient.

## 4. Canonical Language

### 4.1 Symptom

A **symptom** is a behavior, sound, sensation, performance problem, or visible
effect that the user wants BikeDoc to explain.

Examples:

- “The chain skips when I pedal hard.”
- “The front brake rubs once per wheel rotation.”
- “The steering feels loose.”

A symptom is not itself a cause.

### 4.2 Observed Finding

An **observed finding** is a factual condition or cue obtained from an image,
user report, measurement, functional check, repair history, or other provided
evidence.

Examples:

- “Orange-brown discoloration is visible on the chain.”
- “The user reports that skipping occurs only under load.”
- “A chain checker reads beyond the replacement threshold.”
- “Repair history records a recent shift-cable replacement.”

An observed finding describes what was seen, reported, measured, or recorded.
It must not claim that the condition causes a symptom unless the evidence also
supports that causal relationship.

“Observed” in this term does not mean visual-only. The finding's evidence source
must make clear whether it came from an image, user report, measurement,
functional check, repair history, or another source.

### 4.3 Primary Diagnosis

A **primary diagnosis** is the best-supported causal explanation of the user's
main complaint at the time the diagnostic phase completes.

The primary diagnosis must be more than a restatement of an observed finding.
It must connect a component condition or failure mode to one or more reported
symptoms and state an appropriately calibrated confidence.

### 4.4 Contributing Factor

A **contributing factor** is a supported or suspected condition that worsens,
triggers, or combines with another condition to produce the reported symptoms,
but is not presented as the sole or primary explanation.

Contributing factors can be independent conditions. For example, drivetrain
skipping may involve both worn chain-and-cassette engagement and imperfect
derailleur indexing.

A contributing factor is not an alternate hypothesis. Both the primary
diagnosis and a contributing factor may be true simultaneously.

### 4.5 Alternate Hypothesis

An **alternate hypothesis** is a competing causal explanation that remains
possible if the primary diagnosis is incorrect or incomplete.

Alternates describe mutually competing or meaningfully distinct explanations.
They must not be used merely to hold additional conditions believed to be
present and contributing at the same time.

### 4.6 Incidental Finding

An **incidental finding** is a condition worth communicating or recording that
does not currently appear to explain or contribute to the user's complaint.

An incidental finding may still deserve later maintenance, monitoring, or
safety attention. “Incidental” does not mean unimportant.

### 4.7 Diagnostic Relevance

**Diagnostic relevance** describes the current evidence-backed relationship
between an observed finding and the user's symptoms. Allowed values are:

| Value | Meaning |
|---|---|
| `unknown` | The relationship has not been established. |
| `possible_contributor` | The condition could plausibly contribute, but the available evidence does not yet support that conclusion. |
| `supported_contributor` | The evidence supports the condition as one contributor to the symptoms. |
| `incidental` | The condition is present or reported but does not currently appear to contribute to the complaint. |

Diagnostic relevance may change as new evidence arrives. A finding must not be
silently promoted from `unknown` or `possible_contributor` to
`supported_contributor`.

### 4.8 Material Diagnostic Evidence

**Material diagnostic evidence** is evidence reasonably likely to change:

- the primary diagnosis
- the set of contributing factors
- confidence
- safety handling
- DIY suitability
- the appropriate next action

A readily obtainable photo, measurement, or functional observation is
**materially missing** when it is likely to change one of those outcomes.

## 5. Guiding Decisions

### 5.1 Finding Is Not Cause

The presence of an abnormal-looking or abnormal-sounding condition must not by
itself establish that the condition caused the user's complaint.

Temporal proximity, visual salience, model confidence, and agreement between
two inspections of the same image do not independently establish causation.

The agent may relate a finding to the complaint when supported by symptom
pattern, physical measurement, functional behavior, repair history,
contradictory or corroborating evidence, or another diagnostically meaningful
fact.

### 5.2 Communicate Before Certainty

The agent must not hide a meaningful finding merely because its causal role is
uncertain. It should promptly:

1. state the finding in factual language
2. explain why it may matter
3. state the current relationship to the symptoms
4. explain what evidence would clarify that relationship when clarification is
   material

The response must avoid presenting uncertainty so vaguely that the user cannot
tell what was actually noticed.

### 5.3 Investigation Follows the Complaint

The diagnostic investigation should remain organized around the user's
symptoms, not around whichever abnormality is most visually salient.

For example, when the complaint is drivetrain skipping and a photo shows chain
corrosion, the agent should consider whether corrosion or stiff links explain
the skipping while also considering other plausible causes such as wear,
indexing, alignment, or cable friction when supported by the bike and symptom
context.

### 5.4 Multiple Conditions May Be True

The agent must consider whether more than one condition contributes to the
reported symptoms. It must not force every case into a one-cause explanation.

The agent also must not manufacture multiple contributors merely to appear
thorough. Each reported contributing factor requires evidence and calibrated
confidence.

### 5.5 Thoroughness Is Risk-Weighted

The agent must investigate plausible, material, and safety-relevant causes. It
does not need to enumerate or eliminate every theoretically possible cause.

The preferred next request is the single photo, measurement, question, or
functional observation most likely to distinguish important hypotheses or
change the appropriate next action.

### 5.6 Report Completion Is a Decision

Finding an abnormality is not a completion condition.

The diagnostic phase completes only when:

- a useful causal conclusion is supported and further readily obtainable
  evidence is unlikely to materially change it
- the user declines or cannot provide further material evidence and a limited
  report is still useful
- an in-person assessment or safety referral is the appropriate diagnostic
  conclusion

The completion decision must be represented separately from the report's
content through the internal completion basis defined in Section 10.

## 6. Required Agent Behavior

### 6.1 Handling a New Finding

When the agent identifies a meaningful new finding, it must:

1. Describe the finding without causal overreach.
2. Identify its current diagnostic relevance.
3. Tell the user why the finding may matter.
4. Retain the finding for possible inclusion in the final report.
5. Continue investigating when material evidence is still missing.
6. Raise a safety flag immediately when the finding creates a material safety
   concern, regardless of whether its root cause is known.

The agent must not call `save_diagnostic_report` solely because this sequence
identified an abnormal condition.

### 6.2 User-Facing Language

The agent should use language such as:

> The photo shows orange-brown discoloration that may be chain corrosion. That
> could contribute to poor engagement or stiff links, so it is relevant, but
> the photo alone does not establish that it is causing the skipping.

The agent should avoid language such as:

> Your drivetrain is skipping because the chain is rusty.

unless the available evidence actually supports that causal conclusion.

### 6.3 Follow-Up Requests

When more evidence is needed, the agent should ask for one high-value input at
a time. The request must:

- identify the exact question it is intended to answer
- specify the required photo view, measurement, or functional observation
- be reasonably obtainable by the user
- avoid unsafe riding or repair actions

The agent should not present an exhaustive checklist unless the user explicitly
asks for one.

### 6.4 Same-Turn Completion

The agent may complete diagnosis during the same turn in which it notices a
finding only when:

- the finding and other available evidence directly explain the reported
  symptom
- no meaningful competing or simultaneous contributor requires readily
  obtainable material evidence
- safety handling is complete
- the completion basis satisfies Section 8

This rule allows straightforward diagnoses without creating an arbitrary
minimum-turn requirement.

### 6.5 User Declines Further Input

If the user declines or cannot provide requested material evidence, the agent
may produce a limited report when it remains useful. The report must:

- use appropriately low or unknown confidence
- identify unresolved questions
- avoid unsupported causal claims
- distinguish findings from diagnoses
- recommend in-person assessment when required by risk or uncertainty

The completion basis must use `user_declined_more_input`.

## 7. Evidence and Causal Classification

### 7.1 Permitted Evidence Sources

An observed finding in the report must declare one of these evidence sources:

- `image`
- `user_report`
- `measurement`
- `functional_check`
- `repair_history`
- `other`

For image-backed findings, artifact IDs must reference owned artifacts attached
to the repair session. Historical visual projections must follow the evidence
limitations in `image-diagnosis.md`.

### 7.2 Evidence Required for Causal Language

A finding may be described as a `supported_contributor` or included as a
contributing factor only when evidence connects it to the symptoms.

Examples of connecting evidence include:

- the symptom occurs under conditions predicted by the suspected issue
- a measurement supports the relevant failure mode
- a functional check reproduces or isolates the symptom
- repair history increases or decreases the likelihood of the issue
- correcting or isolating the condition changes the symptom
- independent evidence corroborates the same mechanical relationship

A visually apparent condition alone may remain `possible_contributor` when its
mechanical relevance is plausible but untested.

### 7.3 Contradictory Evidence

When evidence contradicts the working diagnosis, the agent must:

- lower confidence as appropriate
- retain or introduce the relevant alternate hypothesis
- request material follow-up evidence when obtainable
- raise `contradictory_evidence` when the conflict affects safety
- avoid completing the report merely by choosing the most salient evidence

### 7.4 Negative Evidence

Extractor silence, lack of visibility, absence from one photo, or failure to
mention a condition must not be treated as evidence that the condition is
absent.

Negative evidence is valid only when the source was capable of evaluating the
condition and the absence itself is diagnostically meaningful.

## 8. Diagnostic Report Readiness

### 8.1 Normal Completion Requirements

Before completing with `diagnosis_supported`, the agent must verify:

1. The user's main reported symptoms are explained by the primary diagnosis,
   contributing factors, or an explicit statement that a symptom remains
   unresolved.
2. The primary diagnosis is supported by more than the mere presence of an
   abnormal finding.
3. Meaningful simultaneous contributing conditions have been considered.
4. Meaningful alternate hypotheses have been considered.
5. Readily obtainable material evidence is no longer missing.
6. Important contradictions and evidence limitations are resolved or
   explicitly retained.
7. Safety-critical possibilities have been evaluated, flagged, or referred.
8. Confidence and DIY suitability match the available evidence.

These requirements do not mandate a minimum number of hypotheses, findings,
turns, artifacts, or evidence sources.

### 8.2 Continuing Investigation

The agent must continue the diagnostic phase instead of saving a report when:

- a new finding has an unclear causal relationship and an obtainable input
  would materially clarify it
- the primary diagnosis is based only on visual salience or co-occurrence
- the reported symptoms are not explained
- an important simultaneous contributor has not been considered
- contradictory evidence could materially change the next action
- a safety-relevant uncertainty requires more evidence

The next assistant response should acknowledge current findings and request the
single most valuable next input.

### 8.3 Referral Completion

The agent may complete with `in_person_assessment_required` when further remote
diagnosis is unsafe, impractical, or unlikely to resolve the important
uncertainty.

The report must clearly distinguish:

- what was observed
- what is suspected
- what remains unknown
- why in-person assessment is required

Safety flags and repair-instruction blocking remain governed by
`safety-diagnostic.md`.

## 9. Diagnostic Report V2

### 9.1 Version

The report schema introduced by this behavior is
`diagnostic_report.v2`.

V2 retains the V1 envelope, server-owned diagnostic session ID, artifact
ownership rules, safety rules, and validation timing. It adds explicit
representation for observed findings, simultaneous contributing factors, and
unresolved questions.

The public OpenAPI contract, report schema spec, diagnostic API examples, ADK
tool contract, backend schemas, and Android generated models must be updated
before V2 is exposed publicly.

### 9.2 Required Top-Level Shape

`DiagnosticReportV2` contains:

| Field | Type | Rules |
|---|---|---|
| `schema_version` | string | Must equal `diagnostic_report.v2`. |
| `primary_diagnosis` | `Diagnosis` | Best-supported causal explanation. |
| `contributing_factors` | array of `ContributingFactor` | Simultaneous contributors; use `[]` when none are supported. |
| `observed_findings` | array of `ObservedFinding` | Meaningful findings retained from the investigation. |
| `alternate_hypotheses` | array of `AlternateHypothesis` | Competing explanations; use `[]` when none remain meaningful. |
| `unresolved_questions` | array of string | Material limitations retained at completion; use `[]` when none remain. |
| `evidence_summary` | string | Concise user-readable synthesis that distinguishes findings from causal conclusions. |
| `repair_estimate` | `RepairEstimate` | Retained from V1 until planning/report contracts are revised separately. |
| `key_artifact_ids` | array of string | Owned diagnostic artifact IDs that materially informed the report. |
| `user_skill_level` | `UserSkillLevel` | Same values as V1. |
| `safety_flags` | array of `SafetyFlag` | Same safety behavior as V1. |
| `diagnostic_session_id` | string | Server-owned phase-session/archive reference. |
| `cost_estimate` | `PlanCostEstimate` or null | Optional server enrichment retained from V1. |

This spec does not move `repair_estimate` or `cost_estimate` to the planning
phase because doing so requires a separate phase-contract decision. Their
presence must not be used as a reason to complete diagnosis prematurely.

### 9.3 Observed Finding

`ObservedFinding` contains:

| Field | Type | Rules |
|---|---|---|
| `component` | string | Component or bicycle area associated with the finding. |
| `finding` | string | Factual condition, cue, measurement, or report without causal overreach. |
| `evidence_source` | enum | One value from Section 7.1. |
| `relationship_to_symptoms` | `DiagnosticRelevance` | Current evidence-backed relevance. |
| `artifact_ids` | array of string | Required for image-backed findings; otherwise normally `[]`. |

Findings should be concise and deduplicated. The report need not contain every
minor observation from the session. It must retain findings that materially
informed the diagnosis, remain relevant to maintenance or safety, or help
explain uncertainty.

### 9.4 Contributing Factor

`ContributingFactor` contains:

| Field | Type | Rules |
|---|---|---|
| `component` | string | Component or area involved. |
| `issue` | string | Condition believed to contribute to the symptoms. |
| `confidence` | `Confidence` | `unknown`, `low`, `medium`, or `high`. |
| `evidence_summary` | string | Concise evidence connecting the condition to the symptoms. |

A `ContributingFactor` must not merely duplicate an observed finding. Its
`evidence_summary` must state why the condition is believed to contribute.

### 9.5 Example

```json
{
  "schema_version": "diagnostic_report.v2",
  "primary_diagnosis": {
    "component": "chain and cassette",
    "issue": "Wear is causing poor engagement and skipping under load.",
    "confidence": "medium",
    "diy_suitability": "caution"
  },
  "contributing_factors": [
    {
      "component": "rear derailleur",
      "issue": "Indexing is slightly out and worsens shifts on the smaller sprockets.",
      "confidence": "medium",
      "evidence_summary": "Skipping is gear-specific and the functional check showed delayed alignment at the affected sprockets."
    }
  ],
  "observed_findings": [
    {
      "component": "chain",
      "finding": "Orange-brown discoloration is visible on several outer plates.",
      "evidence_source": "image",
      "relationship_to_symptoms": "possible_contributor",
      "artifact_ids": ["art_chain_1"]
    },
    {
      "component": "chain",
      "finding": "The chain checker indicates wear beyond the supported replacement threshold.",
      "evidence_source": "measurement",
      "relationship_to_symptoms": "supported_contributor",
      "artifact_ids": []
    }
  ],
  "alternate_hypotheses": [
    {
      "component": "rear derailleur hanger",
      "issue": "Minor hanger misalignment may contribute to gear-specific shifting inconsistency.",
      "confidence": "low",
      "ruled_out_by": null
    }
  ],
  "unresolved_questions": [
    "Rear derailleur hanger alignment was not physically measured."
  ],
  "evidence_summary": "The load-dependent symptom and chain measurement support chain-and-cassette wear as the primary cause. Slight indexing error also contributes. Visible discoloration is retained as a possible contributor but does not independently establish the cause.",
  "repair_estimate": {
    "difficulty": "medium",
    "difficulty_notes": "Final repair requirements depend on confirming cassette wear and correcting indexing.",
    "tools_required": ["chain checker", "hex keys"],
    "parts_required": ["chain", "cassette if matched wear is confirmed"],
    "repair_time": {
      "low_minutes": 45,
      "high_minutes": 120
    },
    "shop_repair_cost": {
      "low_usd": 80,
      "high_usd": 220,
      "notes": "Estimate only; actual pricing depends on parts and local labor."
    }
  },
  "key_artifact_ids": ["art_chain_1"],
  "user_skill_level": "beginner",
  "safety_flags": [],
  "diagnostic_session_id": "phs_123",
  "cost_estimate": null
}
```

## 10. Internal Completion Basis

### 10.1 Purpose

`save_diagnostic_report` must require an internal `completion_basis` alongside
the report. This structure makes the completion decision explicit and
testable. It is not hidden chain-of-thought and must contain only concise
conclusions suitable for ordinary application logs or traces under existing
privacy policy.

`completion_basis` is an internal ADK tool input. It must not be serialized in
the public report payload.

### 10.2 Shape

```json
{
  "completion_basis": {
    "completion_reason": "diagnosis_supported",
    "symptoms_addressed": [
      "Chain skips under load in the three smallest rear sprockets."
    ],
    "contributors_considered": [
      "chain condition",
      "chain wear",
      "cassette wear",
      "derailleur indexing",
      "hanger alignment"
    ],
    "remaining_uncertainties": [
      "Hanger alignment was not physically measured."
    ],
    "next_evidence_likely_to_change_conclusion": false,
    "why_ready": "The symptom pattern and chain measurement support the diagnosis; the remaining alignment uncertainty does not change the immediate recommendation."
  }
}
```

Allowed `completion_reason` values are:

- `diagnosis_supported`
- `user_declined_more_input`
- `in_person_assessment_required`

### 10.3 Validation

The tool boundary must validate:

- `completion_reason` is supported
- `symptoms_addressed` is non-empty
- every list entry is non-blank
- `why_ready` is non-blank
- normal `diagnosis_supported` completion has
  `next_evidence_likely_to_change_conclusion: false`
- the report and completion basis are structurally compatible with the active
  diagnostic phase

For `user_declined_more_input` or `in_person_assessment_required`,
`next_evidence_likely_to_change_conclusion` may be `true`. In those cases, the
report must retain the material limitation in `unresolved_questions`, use
calibrated confidence, and apply required safety or referral behavior.

The backend is not expected to prove the mechanical truth of
`contributors_considered` or `why_ready`. Semantic quality is protected through
the prompt and evaluations in Sections 11 and 13.

### 10.4 Error Behavior

An invalid completion basis must prevent persistence and phase transition. The
tool should return the existing report-validation error category with concise,
field-specific details so the agent can continue the diagnostic phase.

The implementation must not introduce a second public report-completion
endpoint.

## 11. Prompt Requirements

The diagnostic prompt must include the following behavior:

```text
An abnormal observation is not automatically a diagnosis.

When you notice wear, damage, contamination, corrosion, misalignment, or
another abnormal condition:

1. Clearly tell the user what you observed.
2. State whether its relationship to the reported symptoms is unknown,
   possible, supported, or incidental.
3. Retain it as an observed finding.
4. Continue investigating other plausible causes and simultaneous contributing
   factors when material uncertainty remains.
5. Do not call save_diagnostic_report merely because an abnormality was found.

A finding may be identified as a cause or contributing factor only when the
symptom pattern, measurement, functional check, history, or other evidence
supports that relationship.

Before saving a report, verify that the primary diagnosis explains the user's
main symptoms, meaningful simultaneous contributors and alternate hypotheses
have been considered, safety handling is complete, and no readily obtainable
evidence is likely to materially change the conclusion.

When more evidence is needed, acknowledge the current findings and request one
targeted, high-value input instead of saving the report.
```

The prompt must remove or qualify instructions that allow a low-confidence
report solely because evidence is thin. Low confidence is a calibration value,
not permission to stop investigating while readily obtainable material
evidence remains.

Prompt changes must not require exact assistant wording. The semantic behavior
is normative.

## 12. Safety Interaction

Observation-versus-causation uncertainty must not delay safety escalation.

The agent must raise a safety flag as soon as a material safety concern is
identified, even when:

- the exact root cause is unknown
- the finding is only a possible contributor
- further evidence is needed before diagnosis
- the diagnostic report is not ready

Safety messages must distinguish the observed or reported hazard from an
unproven root cause. For example:

> Fluid-like residue is visible near the front caliper, and the user reports
> severe braking loss. The exact leak source is not confirmed, but the bike
> should not be ridden until the brake is inspected.

Safety flag codes, severities, acknowledgement, repair blocking, and session
state remain governed by `safety-diagnostic.md`.

## 13. Testing and Evaluation

### 13.1 Deterministic Tests

Backend and prompt-structure tests must cover:

- `DiagnosticReportV2` validation
- rejection of unsupported diagnostic-relevance and evidence-source values
- image findings requiring owned repair-session artifact IDs
- acceptance of non-image findings with `artifact_ids: []`
- separate serialization of observed findings, contributing factors, and
  alternate hypotheses
- `completion_basis` validation
- rejection of `diagnosis_supported` when
  `next_evidence_likely_to_change_conclusion` is `true`
- allowance for unresolved evidence when the completion reason is
  `user_declined_more_input` or `in_person_assessment_required`
- exclusion of `completion_basis` from the public report
- prevention of persistence and phase transition after completion-basis
  validation failure
- preservation of existing safety validation and artifact ownership behavior
- prompt inclusion of the finding-versus-cause and report-readiness rules

Tests must not assert exact model wording.

### 13.2 Agent Behavior Evaluations

Agent behavior evaluations must include multi-turn cases where:

- a visually corroded chain is the primary cause
- a visually corroded chain is incidental to cassette wear
- corrosion or stiff links and derailleur indexing both contribute
- a clean-looking drivetrain has measurement-confirmed chain wear
- a visually dramatic condition does not match the reported symptom
- a later measurement contradicts the initial working hypothesis
- two independent conditions contribute to one symptom
- a straightforward single-cause case is ready in one turn
- the user declines further evidence
- an unresolved safety concern requires in-person assessment

At minimum, evaluation labels should identify:

- findings that must be communicated
- findings that must not be asserted as causal
- expected primary or acceptable primary diagnoses
- expected contributing factors
- meaningful alternate hypotheses
- acceptable high-value follow-up requests
- whether `save_diagnostic_report` is permitted on each turn
- required safety behavior
- findings that must remain in the final report

### 13.3 Required Behavioral Metrics

Evaluation reporting must include:

- premature report rate
- causal-overreach rate
- required-finding communication rate
- contributing-factor recall
- alternate-hypothesis correctness
- follow-up usefulness
- report finding-retention rate
- safety-critical miss rate
- unnecessary follow-up rate for straightforward cases
- confidence calibration where labeled evidence supports it

A regression in premature report rate, causal-overreach rate, or
safety-critical miss rate blocks promotion of the changed prompt, model, report
schema, or tool behavior until reviewed.

### 13.4 Initial Acceptance Scenarios

For an initial image showing possible chain corrosion alongside a user report
of skipping under load, the default acceptable behavior is:

- tell the user that possible corrosion is visible
- explain that it may matter but does not yet establish the cause
- retain the condition as an observed finding
- consider both competing and simultaneous drivetrain causes
- request one material next input
- do not call `save_diagnostic_report`

For a later turn with measurement and functional evidence supporting multiple
conditions, acceptable behavior is:

- identify the best-supported primary diagnosis
- identify supported simultaneous contributors
- keep other meaningful possibilities as alternate hypotheses
- retain the original corrosion observation with calibrated relevance
- save only when the completion basis satisfies Section 10

## 14. Observability

Production telemetry should record counts and rates, not raw private reasoning:

- diagnostic turns ending in an input request
- diagnostic turns ending in a report
- reports completed in the same turn as the first finding
- number of observed findings, contributing factors, and alternate hypotheses
  in completed reports
- completion reason
- report validation and completion-basis validation failures
- diagnostic turns and elapsed time before completion

Telemetry must not interpret a larger number of hypotheses or turns as
inherently better. It exists to identify premature completion, excessive user
burden, and regressions.

## 15. Rollout and Compatibility

Implementation should proceed in this order:

1. Add agent evaluations and accepted baselines for premature completion,
   causal overreach, and multiple contributors.
2. Update the diagnostic prompt with Section 11.
3. Add internal `completion_basis` validation to `save_diagnostic_report`.
4. Add `DiagnosticReportV2` to backend and OpenAPI contracts.
5. Update Android generated models and report presentation.
6. Enable V2 report production after contract and behavior verification pass.

V1 reports remain readable as immutable historical reports. The backend must
not rewrite a stored V1 report into V2 by inventing finding or contributor
classifications.

During migration, the diagnostic agent must produce the report version selected
by app-owned orchestration. A single report payload must not mix V1 and V2
fields.

Changes to report versions, prompt behavior, completion-basis shape, or
diagnostic relevance values require regression evaluation against the accepted
baseline.

## 16. Acceptance Criteria

This spec is satisfied when:

- meaningful findings are promptly explained without unsupported causal claims
- findings remain representable in the final report even when incidental or
  only possibly related
- simultaneous contributing factors are represented separately from alternate
  hypotheses
- an abnormal finding alone cannot justify report completion
- report completion includes a valid internal completion basis
- material, readily obtainable evidence produces a targeted input request
  rather than a premature report
- straightforward cases can still complete without arbitrary extra turns
- safety escalation remains immediate and backend-enforced
- deterministic tests and agent evaluations protect the behavior
- no specialist-agent, exhaustive-playbook, or persisted causal-graph system is
  required for the initial implementation

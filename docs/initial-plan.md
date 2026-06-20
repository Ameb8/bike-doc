# Home Bike Repair AI Agent — Design Document

## 1. Overview

An Android app that helps users diagnose and repair bicycle issues through an
AI-guided workflow: identify the problem, decide whether it's safe/worthwhile
to DIY, estimate cost, and walk through the repair step-by-step with
photo/video verification along the way.

The system is built as a **sequential, phase-based agent pipeline** rather
than a flat multi-agent swarm. Each phase is its own context boundary with
its own tools, and hands a small structured artifact forward to the next
phase instead of passing along its full working history. This keeps context
windows lean, reduces cost (especially image tokens), and avoids diluting
each phase's instruction-following with irrelevant history from earlier
phases ("context rot").

---

## 2. User-Facing Flow

1. **Intake** — user selects/adds a bike (from saved profiles or new), gives
   a basic description of the issue (text, photo, optional video/audio).
2. **Diagnostic phase** — conversational back-and-forth with the diagnostic
   agent: clarifying questions, requests for specific photos, narrowing down
   the cause. Ends with a confirmed (or best-guess) diagnosis.
3. **Safety check** — if the issue is dangerous (e.g., cracked carbon frame,
   failing brakes, e-bike battery damage) or clearly beyond DIY scope, the
   user is warned and steered toward a shop. This can also trigger later,
   mid-repair, if something unexpected turns up.
4. **Planning & cost estimation** — parts needed, tools needed (cross-checked
   against what the user already owns), DIY cost estimate vs. shop estimate.
   User decides: DIY or take it to a shop.
5. **Guided repair (DIY path only)** — step-by-step instructions, with photo
   verification checkpoints between steps so mistakes are caught early
   instead of at the end.
6. **Completion** — repair logged to the bike's history; profile and tool
   inventory updated (e.g., newly bought tools are now "owned").

---

## 3. Feature Set

### Core features
- Bike profile per user (make/model/groupset/wheel size), created once and
  reused — avoids re-asking static info every session.
- Tool inventory tracked per user, reused across repairs.
- Repair/maintenance history log per bike, used as diagnostic context
  ("you replaced this chain 200 miles ago").
- Image-based diagnosis (photos of components, drivetrain, brakes, etc.).
- Skill-level calibration (asked once, used to set explanation depth).
- Safety/danger escalation, available at any phase, not just after initial
  diagnosis.
- Step-by-step repair guidance with **photo verification at each step**
  before advancing.
- Cost estimation: parts pricing, owned-vs-needs-to-buy tool diffing,
  buy/borrow/rent recommendation for rarely-needed specialty tools, and a
  DIY-vs-shop cost/time comparison.
- Shop referral/handoff when DIY isn't advisable.
- Torque spec / manufacturer manual retrieval (RAG over service docs) to
  ground repair steps in real spec data rather than model recall alone —
  important for safety-critical torque values (carbon, disc brakes).

### Stretch goals
- Audio-based diagnosis (clicking/grinding sound classification). Lower
  priority — audio classification for mechanical faults is a harder, less
  reliable problem than image-based diagnosis or guided Q&A, and a wrong
  audio-based diagnosis is worse than not offering it.
- Video upload support for harder-to-photograph symptoms (e.g., wheel wobble
  while spinning).

### Explicitly out of scope (for now)
- Live shop quote integration via shop APIs — not realistically available
  for most local bike shops. Shop cost estimates use a maintained reference
  table of typical labor charges instead, presented as a range with a
  disclaimer.

---

## 4. Agentic Architecture Overview

```
 ┌──────────────┐     diagnostic        ┌──────────────┐     plan/cost      ┌──────────────┐
 │  Diagnostic  │ ───   report     ───▶ │   Planning   │ ───   report  ───▶ │  Execution   │
 │    Phase     │                       │ & Cost Phase │                    │    Phase     │
 └──────────────┘                       └──────────────┘                    └──────────────┘
       │                                       │                                    │
       ▼                                       ▼                                    ▼
  (image analysis,                    (parts/labor price            (step sequencing,
   follow-up Q&A tools)                 lookup, inventory               verification-photo
                                         diff tools)                     analysis tools)
```

Each box is a distinct context window with its own tool set. Data flows
**forward only**, as a structured report — not as a replayed transcript.
Full transcripts (especially image-heavy diagnostic sessions) are archived
and retrievable on demand via a lookup tool, but are not preloaded into
downstream phases "just in case."

This is *not* a parallel multi-agent swarm — there's no benefit to running
these concurrently, since each phase strictly depends on the output of the
last. The reason for splitting them at all is **context isolation**, not
parallelism: each phase has a different job, different tools, and a
different "shape" of working memory, and bundling them into one long-running
context would mean every phase pays (in tokens, cost, and attention) for
work that's irrelevant to it.

### Why not one single agent with a state machine?

Was considered and rejected as the primary structure, mainly because:
- Diagnostic sessions with a non-expert user can be long and exploratory
  (many photo requests, false starts, clarifying questions) — most of that
  detail is irrelevant once a diagnosis is confirmed.
- Image tokens are the dominant cost driver, and diagnostic-phase images
  are mostly disposable once the diagnosis is locked in.
- The execution phase needs precise, procedural instruction-following
  (torque specs, step order, safety checks) — keeping that context clean
  and free of an earlier meandering conversation measurably helps reliability.

A single continuous context only would have been preferable if phases
needed to share large amounts of live, evolving context with each other —
they don't; a well-designed report is sufficient.

---

## 5. Phase 1: Diagnostic

**Inputs:** bike profile, repair history, user's initial issue description,
photos/video.

**Tools:** image analysis, follow-up question generation, (optionally) audio
classification.

**Behavior:** Iteratively narrows down the issue — asks clarifying
questions, requests specific photos ("now from this angle"), cross-checks
against repair history. Tracks alternate hypotheses, not just a single
guess, so ambiguity isn't silently dropped.

**Output — Diagnostic Report:**

```json
{
  "primary_diagnosis": {
    "component": "rear derailleur hanger",
    "issue": "bent, causing inconsistent shifting",
    "confidence": "high"
  },
  "alternate_hypotheses": [
    {"component": "rear derailleur cable tension", "ruled_out_by": "B-tension was correct"}
  ],
  "evidence_summary": "Chain skips under load in gears 2-4, hanger visibly off-axis in photo 3.",
  "key_photos": ["photo_3_hanger_angle", "photo_5_drivetrain_full"],
  "user_skill_level": "beginner",
  "safety_flags": [],
  "diagnostic_session_id": "for archival lookup, not loaded by default"
}
```

The full diagnostic transcript (all Q&A turns, all photos) is archived and
addressable by `diagnostic_session_id`, but is not part of what downstream
phases automatically receive.

---

## 6. Phase 2: Planning & Cost Estimation

**Inputs:** diagnostic report, bike profile, tool inventory.

**Tools:** parts price lookup/search, labor-cost reference table, inventory
diffing.

**Behavior:** Resolves the diagnosis into a concrete parts/tools list,
diffs it against the user's owned tools, classifies missing tools as
"buy," "borrow/rent recommended" (for rarely-needed specialty tools), or
"required purchase." Produces a DIY vs. shop cost and time comparison. This
phase is also the **decision gate** — if the user opts for a shop, the
execution phase is never invoked.

**Output — Plan Report:**

```json
{
  "diagnosis_summary": "rear derailleur hanger bent",
  "parts_needed": [
    {"item": "derailleur hanger (model-specific)", "owned": false, "est_price": "12-18"}
  ],
  "tools_needed": [
    {"item": "5mm hex wrench", "owned": true},
    {"item": "derailleur alignment gauge", "owned": false, "action": "borrow_recommended"}
  ],
  "diy_total_estimate": "12-18",
  "shop_estimate": "35-55 (parts + labor)",
  "shop_time_estimate": "same day, most shops",
  "recommendation_basis": "low risk, beginner-friendly, most tools already owned",
  "user_decision": "diy"
}
```

This report embeds the diagnosis essentials, so the execution phase doesn't
need a separate reference back to the original diagnostic report — data
flows forward as a single, progressively-distilled chain rather than being
broadcast to multiple downstream consumers independently.

---

## 7. Phase 3: Repair Execution

**Inputs:** plan report (only — diagnosis details arrive embedded in it),
bike profile.

**Tools:** step sequencing/instruction generation, manual/torque-spec
retrieval (RAG), verification-photo analysis.

**Behavior:** Walks the user through the repair step-by-step. After each
step, requests a verification photo before advancing, so errors are caught
immediately rather than at the end. Can escalate back to a (re-seeded)
diagnostic pass if a verification photo reveals something unexpected — this
reuses the same report-handoff pattern, just triggered mid-flow.

**On completion:** writes a summary back into the bike's repair history and
updates the tool inventory (e.g., a newly purchased alignment gauge is now
"owned").

---

## 8. Persistent Data Stores

| Store | Contents | Why persistent |
|---|---|---|
| **Bike profile** | Make/model/groupset/wheel size, per bike | Avoids re-asking static specs every session |
| **Tool inventory** | Tools the user owns, per user | Avoids re-asking ownership every repair; drives buy/borrow logic |
| **Repair history** | Past repairs/maintenance, per bike | Informs future diagnosis (e.g., recently replaced parts) |
| **Diagnostic session archive** | Full transcripts + photos, keyed by session ID | Lazily retrievable detail, not loaded by default downstream |

---

## 9. Safety & Escalation

Escalation to "this should go to a shop" is not a one-time check after
initial diagnosis — it's a condition that can fire at **any phase**:

- During diagnosis (e.g., frame crack visible in a photo)
- During planning (e.g., cost/complexity clearly not worth DIY)
- During execution (e.g., a verification photo reveals something
  unanticipated, like a stripped bolt or hidden corrosion)

Particular caution areas: carbon frame/components, hydraulic disc brakes,
e-bike battery systems, suspension internals — these carry real injury or
failure risk if done incorrectly and should bias toward shop referral.

---

## 10. Design Principles Recap

- **Phase = context boundary**, not just a logical step — each phase gets
  only what it needs, not everything that came before.
- **Structured reports over transcripts** — every phase ends by producing a
  small, well-defined artifact; raw working history is archived, not
  forwarded.
- **Sequential handoff, not broadcast** — each phase consumes only the
  immediately preceding report (which itself embeds what came before it),
  not multiple independent copies of upstream context.
- **Decision gates are phase boundaries** — the DIY-vs-shop choice naturally
  sits at the planning→execution boundary, so a "shop" decision can skip
  execution entirely.
- **Disposable vs. durable data** — diagnostic transcripts/photos are
  disposable (archived, lazily retrievable); bike profile, tool inventory,
  and repair history are durable and reused across sessions.

---

## 11. Open Questions / Future Considerations

- Exact schema/source for the labor-cost reference table (manual research
  vs. periodically updated dataset).
- Whether to support multi-bike households sharing a tool inventory.
- Local shop directory/referral integration (could double as a freelance or
  partnership channel down the line).
- How much of the RAG corpus (manufacturer manuals/torque specs) needs to be
  pre-indexed vs. fetched live per repair.
- Offline behavior for users working in a garage with poor connectivity.
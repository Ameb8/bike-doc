# Bike Doc Cost Estimation And Listing Lookup Spec

Status: Draft v0.1
Last updated: 2026-07-04

This spec defines the product behavior and architecture for estimating required
tool and part cost during the planning phase. It focuses on how Bike Doc turns
structured repair requirements into user-visible pricing and listing data using
fresh, grounded Gemini web-search lookup calls behind a provider-neutral
backend interface.

This document does not define route handlers, provider SDK choices, prompt
text, database schema details, or concrete code structure. Those belong in
follow-up implementation specs.

## References

- Product design: `docs/specs/bike-doc.md`
- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic tool contracts: `docs/specs/apps/adk-diagnostic-tools.md`
- Public API contract: `docs/specs/openapi.yaml`

## 1. Purpose

Bike Doc should give users practical, current cost guidance for the tools and
parts required by a repair plan. The goal is to improve over rough static
estimates by showing grounded market prices, example listings, and freshness
metadata while keeping compatibility reasoning and safety decisions separate.

The system should support:

- required tool cost estimation
- required part cost estimation
- one or more concrete example listings per required item
- retailer link display
- freshness and confidence indicators
- plan-level cost rollups derived from item-level results

Pricing remains an estimate, not a quote or guarantee of availability.

## 2. Scope

In scope:

- planning-phase cost estimation for required tools and parts
- item-by-item lookup using fresh grounded search context
- normalized listing results for user display
- aggregate DIY cost calculation from tool and part estimates
- explicit confidence and ambiguity handling
- support for both exact-match and generic-item pricing

Out of scope:

- diagnostic-phase pricing
- shop labor pricing beyond broad existing plan estimates
- automatic compatibility guarantees
- checkout, cart, or purchasing flows
- user-owned tool inventory
- retailer integrations that require direct transactional APIs

## 3. Guiding Decisions

### 3.1 Cost Estimation Belongs To Planning

Cost lookup is not part of diagnosis. The diagnostic phase produces a
structured report of the likely issue and relevant evidence. The planning phase
turns that report into required tools, required parts, safety considerations,
and cost guidance.

### 3.2 Fresh Context Per Lookup Item

Each required tool or part should be looked up in fresh, narrow context rather
than inside a broad multi-purpose planning conversation. This reduces noise and
keeps search behavior focused on one requirement at a time.

The architecture should therefore treat item lookup as a sequence of isolated,
grounded lookup calls, each operating on one normalized requirement.

### 3.3 Structured Requirements Before Search

Web search should not be responsible for inventing the requirement itself. The
planning phase should first produce a structured requirement for each tool or
part with as much specificity as the system can justify from the diagnosis,
bike profile, and repair plan.

### 3.4 Search Results Are Evidence, Not Compatibility Proof

Grounded web search can find plausible listings and observed prices. It must
not be treated as authoritative proof that a part fits the user’s bike or that
a generic listing is the correct variant unless the requirement is already
sufficiently specific.

### 3.5 Provider-Neutral Backend Boundary

The planning agent and user-visible plan report should depend on a stable
backend pricing interface rather than raw Gemini search output. This preserves
the ability to replace or augment search later with other sources.

## 4. End-To-End Behavior

The intended flow is:

1. Diagnostic phase completes and produces a structured diagnostic report.
2. Planning phase consumes the diagnostic report, bike profile, and repair
   history as needed.
3. Planning identifies required tools and parts.
4. Planning normalizes each requirement into a pricing-ready item record.
5. The backend performs a fresh grounded listing lookup for each item.
6. The backend returns normalized listing and observed-price results per item.
7. Planning aggregates those results into a plan report with item-level and
   total estimated cost.
8. The user sees both individual item pricing evidence and rollup estimates.

## 5. Requirement Normalization

Before lookup, each required item should be represented in a structured form.

Each requirement should capture, when known:

- item type: `tool` or `part`
- display name
- category
- quantity
- whether a generic equivalent is acceptable
- whether an exact match is required
- brand, model, or specification details
- bike-specific compatibility notes
- planning confidence
- search query text suitable for grounded lookup

Examples:

- tool: `Chain checker`
- tool: `Cassette lockring tool`
- part: `Shimano HG54 10-speed chain`
- part: `700x38c tube, presta valve`

The planning phase may produce requirements at different specificity levels:

- exact item known
- constrained item family known
- generic item only

This specificity level materially affects lookup confidence and how results
should be presented to the user.

## 6. Lookup Architecture

### 6.1 One Narrow Lookup Per Item

Each required item should be priced through its own fresh grounded lookup call.
The lookup receives the normalized requirement only, plus limited plan context
needed to disambiguate the item. It should not receive the full diagnostic
transcript.

### 6.2 Grounded Search As The First Live Source

The first live implementation should use grounded Gemini web search to locate:

- plausible current listings
- observed prices
- retailer names
- product titles
- source links

This is a search-backed pricing source, not a merchant catalog system.

### 6.3 Backend Normalization Of Results

Raw model output and raw search-grounding details should be normalized into a
stable Bike Doc result model before they are used in plan generation or shown
to the user.

### 6.4 Optional Fallback To Cached Or Manual Estimates

When grounded search does not return a sufficiently confident result, the
system may fall back to:

- a recent cached lookup result
- a manual estimate table for common items
- a broader price range with lower confidence
- an explicit “price unavailable” outcome

The backend should prefer transparent degradation over manufactured precision.

## 7. Matching Rules

### 7.1 Exact-Match Required

Some parts require an exact or near-exact listing match before a concrete
observed price should be shown as the primary estimate. Examples include:

- drivetrain components with known speed/series requirements
- brake pads with known compound and fit requirements
- proprietary small parts

If the system lacks enough detail to identify a likely exact match, it should
either:

- return lower-confidence generic estimates, or
- ask for additional planning information before pricing that item precisely

### 7.2 Generic-Acceptable Items

Some items can be priced using a generic or near-generic listing, such as:

- tire levers
- chain lube
- shop rags
- hex-key set

For these items, the system may choose a representative listing when exact
brand/model matching is unnecessary.

### 7.3 Ambiguity Handling

If search returns multiple materially different item variants, the system
should not collapse them into a false single answer. It should instead:

- prefer the most plausible match only when confidence is adequate
- include alternates when helpful
- mark compatibility uncertainty explicitly

## 8. Item Result Model

Each normalized lookup result should describe one required item and the pricing
evidence found for it.

### 8.1 Required Fields

Each item result should include:

- item type: `tool` or `part`
- requirement display name
- quantity
- estimate status
- estimate confidence
- result freshness timestamp

### 8.2 Listing Evidence

Each item result may include one primary listing and zero to two alternate
listings.

Each listing should include:

- product title
- retailer name
- observed price
- currency
- source URL
- observed timestamp
- match confidence
- short match rationale

### 8.3 Estimate Status

Each item result should have one of these statuses:

- `priced_listing_found`
- `range_estimate_only`
- `cached_estimate_used`
- `price_unavailable`
- `needs_more_detail`

### 8.4 Compatibility And Ambiguity Flags

Each item result should include explicit flags when relevant:

- compatibility uncertain
- search match ambiguous
- generic substitute used
- exact match not confirmed

## 9. Aggregate Cost Model

The planning phase should compute plan-level rollups from item-level results.

At minimum, the plan should distinguish:

- estimated total parts cost
- estimated total required tool cost
- estimated total DIY out-of-pocket cost

The aggregation should use the best available estimate for each item:

- primary observed listing price when confidence is adequate
- otherwise a range estimate or fallback estimate

If an item has uncertain pricing, the plan should remain usable without
pretending the total is exact. The total may therefore be expressed as:

- a point estimate, when all required items are sufficiently grounded
- a range, when one or more items remain uncertain

## 10. User-Facing Presentation Rules

Pricing should be presented as observed market evidence, not absolute truth.

User-visible pricing should show:

- item name
- observed price or estimated range
- retailer
- source link
- last-checked date

When uncertainty exists, the UI should make it legible through text and state
rather than burying it in small print.

Recommended phrasing:

- `Observed price`
- `Estimated range`
- `Source`
- `Last checked`
- `Compatibility not confirmed`

The system should avoid claims such as:

- exact fit guaranteed
- cheapest available price
- in-stock guarantee
- final checkout total

## 11. Freshness And Caching

Price lookups are time-sensitive and should be treated as stale after a bounded
window. Cached results may be reused only while still fresh according to
backend policy.

Freshness behavior should support:

- a recorded observation timestamp for each listing
- a recorded lookup timestamp for each item result
- a configurable staleness window
- re-query after staleness rather than silent indefinite reuse

Different item types may later justify different staleness windows, but the
behavior should be consistent from the user’s perspective: every price shown
must have a last-checked time.

## 12. Failure And Degradation Behavior

The pricing system must fail gracefully.

Expected degradation cases include:

- no reliable listing found
- search results too ambiguous
- requirement too vague
- transient lookup failure
- stale cached result with no fresh replacement

In these cases, the plan should still complete when possible, with explicit
reduced confidence. Pricing failure for one item should not automatically block
the entire planning phase unless that item is essential to a safety-critical
decision and no usable estimate can be produced.

## 13. Safety And Policy Constraints

Pricing must not override safety behavior. A live listing with a plausible
price does not make a repair appropriate for DIY.

The system must also avoid:

- inventing torque specs from retailer pages
- inferring bike-part compatibility solely from listing text
- turning a vague repair need into a highly specific part without clear basis

When safety-critical parts are involved and compatibility is uncertain, the
system should lower confidence and may steer the plan toward shop referral or
additional verification.

## 14. Quality Rationale

This architecture chooses fresh item-level lookup context over one large
multi-purpose agent context because it improves quality in the ways that matter
for pricing:

- narrower search tasks reduce conversational noise
- item-level lookup is easier to validate
- ambiguity is contained to one item instead of contaminating the full plan
- grounded pricing evidence can be cached and audited independently

This design also keeps implementation burden moderate by avoiding the need for
long-lived sub-agents per item. The system gets most of the quality benefit of
fresh context while preserving a simpler planning architecture.

## 15. Example Output Shape

The exact schema is a follow-up detail, but a plan-level cost result should be
able to represent a shape like:

```json
{
  "parts_total": {
    "amount": 36.98,
    "currency": "USD"
  },
  "tools_total": {
    "amount": 17.95,
    "currency": "USD"
  },
  "diy_total": {
    "amount": 54.93,
    "currency": "USD"
  },
  "items": [
    {
      "item_type": "part",
      "requirement_name": "Shimano HG54 10-speed chain",
      "quantity": 1,
      "status": "priced_listing_found",
      "estimate_confidence": "high",
      "compatibility_uncertain": false,
      "primary_listing": {
        "title": "Shimano HG54 10-Speed Chain",
        "retailer": "Example Retailer",
        "observed_price": 27.99,
        "currency": "USD",
        "url": "https://example.com/product/shimano-hg54",
        "observed_at": "2026-07-04T00:00:00Z",
        "match_confidence": "high",
        "match_rationale": "Listing title matches required model and speed."
      }
    },
    {
      "item_type": "tool",
      "requirement_name": "Chain checker",
      "quantity": 1,
      "status": "priced_listing_found",
      "estimate_confidence": "medium",
      "generic_substitute_used": true,
      "primary_listing": {
        "title": "Chain Wear Indicator Tool",
        "retailer": "Example Retailer",
        "observed_price": 17.95,
        "currency": "USD",
        "url": "https://example.com/product/chain-checker",
        "observed_at": "2026-07-04T00:00:00Z",
        "match_confidence": "medium",
        "match_rationale": "Representative generic chain checker listing."
      }
    }
  ]
}
```

## 16. Follow-Up Specs

This behavior spec should be followed by implementation-facing specs for:

- planning-phase tool contracts
- public/API plan report schema updates, if needed
- persistence and caching rules for price lookup results
- evaluation scenarios for pricing accuracy and ambiguity handling
- provider-specific wiring for grounded Gemini search

# BikeDoc Local Diagnostic Trace Content

Status: Canonical v1.0
Last updated: 2026-08-11

This document defines a small, local-development-only mechanism for including
diagnostic-agent message content and tool payloads in OpenTelemetry traces. Its
purpose is to let a developer inspect what the model received, what it returned,
which tool arguments it produced, and how each tool responded while debugging a
locally run BikeDoc diagnostic session.

Within this narrow scope, this document supersedes the no-content requirements
in `docs/specs/apps/diagnostic-telemetry.md` only when the application is running
with `environment: local` and the explicit content-capture setting is enabled.
All other telemetry privacy and production-safety rules remain unchanged.

## References

- Diagnostic telemetry: `docs/specs/apps/diagnostic-telemetry.md`
- Configuration: `docs/specs/apps/config-setup.md`
- Diagnostic observation behavior:
  `docs/specs/apps/agent/diagnostic-observation-handling.md`
- Backend organization: `docs/specs/apps/api.md`

## Goals

- Make local diagnostic traces useful for understanding actual agent behavior.
- Show model request and response content and ADK tool arguments and responses.
- Require an explicit opt-in each time a developer configures a local runtime.
- Make accidental content capture in deployed or production-like environments
  fail during application startup.
- Reuse ADK and OpenTelemetry content capture rather than building a custom
  transcript, redaction, or storage system.

## Non-Goals

This feature does not add:

- production prompt, response, or tool-payload capture
- capture of real-user sessions for later debugging
- a new database table, trace viewer, exporter, or retention system
- a custom payload redaction framework
- chain-of-thought or private model-reasoning capture
- application logging of prompts or tool payloads
- selective capture by user, repair session, turn, tool, or sampling rule
- guarantees that every model provider or trace viewer renders content in the
  same shape

Developers must use synthetic or personally controlled local test data when
content capture is enabled.

## Configuration

Add this typed setting to `bike_doc_api.core.config.Settings`:

```python
diagnostic_trace_content: bool = False
```

It is configured with:

```text
BIKE_DOC_API_DIAGNOSTIC_TRACE_CONTENT=false
```

The setting must be documented in the repository-root `.env.example` next to
the existing telemetry settings. Its default must be `false`.

Content capture is enabled only when all of the following are true:

1. `BIKE_DOC_API_ENVIRONMENT` normalizes exactly to `local`.
2. `BIKE_DOC_API_DIAGNOSTIC_TRACE_CONTENT` is `true`.
3. OpenTelemetry tracing is configured normally, including a developer-chosen
   local OTLP receiver when exported traces are desired.

The feature does not automatically start or configure a trace backend.

## Runtime Safety Gate

Application startup must fail with a clear configuration error when
`diagnostic_trace_content` is `true` and `environment` is anything other than
`local`.

This check must occur before the application accepts requests or invokes a
model. Values such as `development`, `dev`, `test`, `staging`, and `production`
must not be treated as aliases for `local`.

The check must be owned by the typed settings or diagnostic telemetry startup
validation. Individual routes, agents, or tools must not read the environment
variable directly.

Existing checks that reject independent environment-variable overrides of ADK
or Google GenAI content capture must remain. The BikeDoc-owned setting is the
only supported way to opt in, so an unreviewed process-level instrumentation
override cannot bypass the environment gate.

## Capture Behavior

When the setting is disabled, every diagnostic ADK invocation must continue to
use `ContentCapturingMode.NO_CONTENT` exactly as it does today.

When the setting is enabled in `local`, every diagnostic ADK invocation must
use ADK's span-only message-content capture mode. The resulting ADK model and
tool spans should include the content made available by the installed ADK
instrumentation, including:

- system and developer instructions supplied to the diagnostic agent
- user message content supplied to the agent
- model response content
- tool names and tool-call arguments
- tool response payloads, including validation details returned to the agent

Content must be attached only to trace spans. BikeDoc and ADK telemetry logs
must continue to omit message content and tool payloads so normal console and
JSON logs do not become a second content store.

The implementation should use the installed ADK `TelemetryConfig` and
`ContentCapturingMode` APIs. It must not copy ADK events into custom BikeDoc
span attributes or maintain a parallel transcript.

The diagnostic runner must derive its `RunConfig` from the application setting
for each invocation. A process restart is allowed to be required after changing
the setting; dynamic runtime toggling is not required.

## Data Handling

Local trace content may contain user text, bike-profile data, report content,
tool errors, artifact identifiers, image data or references, and provider
context. It must therefore be treated as sensitive local development data even
though production use is prohibited.

The implementation does not need a custom redactor because the feature is
restricted to synthetic or developer-controlled local sessions. It must not,
however, deliberately add HTTP authorization headers, environment variables,
provider credentials, signed storage URLs, or database connection strings to
traces.

Large binary payload handling is owned by ADK and the configured telemetry
backend. This initial implementation does not need custom truncation or binary
filtering. If local traces become unusably large, that must be addressed in a
later spec based on observed behavior rather than preemptive infrastructure.

## Failure Behavior

- Failure to initialize content capture must fail local startup with a clear
  configuration error rather than silently falling back to an ambiguous mode.
- Telemetry export failure after successful startup must retain the existing
  behavior: it must not change diagnostic product behavior.
- Content capture must not alter tool execution, report validation, persistence,
  phase transitions, or user-visible agent output.
- Disabling the setting and restarting must restore no-content tracing.

## Required Tests

Deterministic backend tests must cover:

1. The setting defaults to `false`.
2. `environment: local` plus an enabled setting selects ADK span-only content
   capture.
3. Disabled capture selects `NO_CONTENT` in every environment.
4. Enabled capture fails startup for `test`, `development`, `staging`, and
   `production`.
5. Existing unsupported ADK and Google GenAI environment overrides remain
   rejected.
6. A real in-memory ADK trace contains representative local user text, model
   response content, tool arguments, and tool response content when enabled.
7. The same trace contains none of those values when disabled.
8. Structured telemetry logs do not contain the representative captured
   values in either mode.

Tests must use synthetic sentinel strings and an in-memory exporter. They must
not call an external model or telemetry service.

## Local Usage

A developer who already has a local OTLP receiver may enable inspection with:

```dotenv
BIKE_DOC_API_ENVIRONMENT=local
BIKE_DOC_API_TELEMETRY_EXPORTER=otlp
BIKE_DOC_API_TELEMETRY_OTLP_ENDPOINT=http://localhost:4318
BIKE_DOC_API_DIAGNOSTIC_TRACE_CONTENT=true
```

After restarting the API and running a diagnostic turn, the developer can open
the `bike_doc.diagnostic.agent.run` descendants in the local trace viewer and
inspect ADK model and `execute_tool` spans. Setting
`BIKE_DOC_API_DIAGNOSTIC_TRACE_CONTENT=false` and restarting returns the runtime
to the default privacy-safe behavior.

## Completion Criteria

This feature is complete when:

- the typed, off-by-default setting and `.env.example` documentation exist
- non-local startup cannot enable trace content
- local diagnostic ADK spans expose model and tool content when explicitly
  enabled
- logs remain content-free
- disabled operation remains identical to the current no-content policy
- all required deterministic tests pass


# Bike Doc Evaluation Datasets

This directory contains evaluation datasets for Bike Doc diagnostic agent
behavior. Agents CLI uses these files to exercise the backend-owned ADK agent
entrypoint, but the product runtime remains the custom FastAPI turn API under
`apps/api`.

Profile-inference evaluation assets are under `../profile-inference`. They use
a versioned label schema and are evaluated by the reusable runner, rather than
by pytest assertions over provider responses.

## Drivetrain Profile Inference

Run all three drivetrain tracers before accepting a drivetrain extractor or
registry change:

```bash
task eval:drivetrain-topology
task eval:drivetrain-roles
task eval:drivetrain-specifications
```

The specifications tracer covers counted chainring, sprocket, and speed facts;
marked driver-interface and bottom-bracket facts; disagreement; loose parts;
and abstention. Each tracer has a committed accepted baseline.

## Rear-brake profile inference

Run the recorded deterministic response fixture and inspect the report with:

```bash
task eval:rear-brake
```

For a new extractor, output schema, prompt, model, preprocessing, registry, or
resolver-policy version, update the version metadata in the dataset and run:

```bash
UV_CACHE_DIR=/tmp/bike-doc-uv-cache uv run --project apps/api python evals/bike-doc/profile-inference/evaluate.py \
  --dataset evals/bike-doc/profile-inference/rear-brake-shadow-v1.json \
  --predictions evals/bike-doc/profile-inference/rear-brake-shadow-v1.responses.json \
  --baseline evals/bike-doc/profile-inference/rear-brake-shadow-v1.baseline.json \
  --output /tmp/bike-doc-rear-brake-evaluation.json
```

The command reports a missing baseline explicitly and never treats it as a
regression pass. Add `--accept` only after reviewing the report; changing
versions requires a comparison against the prior accepted baseline. Use
`--accept --accept-initial-baseline` only when creating the first baseline.

## Running Evaluations

### Default Dataset
```bash
# Generate traces using the default dataset
agents-cli eval generate
agents-cli eval grade
```

### Custom Dataset
```bash
# Generate traces for a custom dataset
agents-cli eval generate --dataset evals/bike-doc/datasets/custom-dataset.json --output custom_traces/
agents-cli eval grade --metrics general_quality --traces custom_traces/
```

## Dataset Format

Each dataset file follows the Gemini Enterprise Agent Platform Evaluation
dataset format. An eval case may use **either** of two shapes — both are
valid input to `agents-cli eval generate`:

**Shape A — single-prompt case:**

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "prompt": {
        "role": "user",
        "parts": [{"text": "User message"}]
      }
    }
  ]
}
```

**Shape B — continued-conversation case (the "N+1" pattern):**
The case carries prior turns in `agent_data` and the last turn ends with a
user message; `eval generate` appends the next agent response.

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "agent_data": {
        "turns": [
          {
            "turn_index": 0,
            "events": [
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "First user message"}]}},
              {"author": "agent", "content": {"role": "model", "parts": [{"text": "First agent reply"}]}},
              {"author": "user",  "content": {"role": "user",  "parts": [{"text": "Follow-up user message"}]}}
            ]
          }
        ]
      }
    }
  ]
}
```

## Key Fields

- `eval_cases`: Array of evaluation cases.
- `eval_case_id`: Unique identifier for the evaluation case (optional).
- `prompt`: A single user message — Shape A.
- `agent_data.turns`: Prior conversation turns ending with a user message — Shape B.

## Creating Custom Datasets

You can create custom datasets in two ways:

1. **By Hand**: Copy `basic-dataset.json` as a template and manually add evaluation cases.
2. **Synthesize**: Use the synthetic dataset generation command to generate conversation scenarios:
   ```bash
   agents-cli eval dataset synthesize --count 10
   ```

## Discovering Metrics

You can discover available out-of-the-box evaluation metrics by running:

```bash
agents-cli eval metric list
```

## Beyond Generate and Grade

Once you have a baseline, the eval surface has a few more commands worth knowing about:

- `agents-cli eval compare BASE CAND` — diff two grade-results files (regression check).
- `agents-cli eval analyze RESULTS` — cluster failure modes from a grade-results file.
- `agents-cli eval optimize` — auto-tune your agent's prompts using eval data.

See the [Evaluation Guide](https://google.github.io/agents-cli/guide/evaluation/) for the full surface and metric reference.

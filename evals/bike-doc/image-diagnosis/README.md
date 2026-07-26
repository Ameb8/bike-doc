# Real-image diagnosis evaluation

This is the paired offline evaluation for diagnostic visual evidence. It is not
a backend unit-test suite and it never accepts recorded model responses as an
evaluation input. For every case, `evaluate.py` reads the approved raster files,
verifies their SHA-256 values, and supplies the same image bytes to an injected
production-equivalent executor once in `pixels_only` mode and once in `enabled`
mode.

Real image files are private evaluation data and are intentionally excluded
from Git. A qualified data steward stores them below `images/`, records the
source/handling approval in the manifest, and supplies their immutable hashes.
Do not use user uploads, public URLs, screenshots of model output, synthetic
images, or recorded JSON responses as a baseline substitute.

## Dataset contract

`dataset.json` uses `bike_doc_image_diagnosis_eval.v1`. Each case has a stable
ID, `bike_group_id`, split, image paths and checksums, coverage tags, and
ground truth sourced from a physical measurement, confirmed repair outcome, or
qualified human review. The validator rejects a bike group appearing in more
than one split. Keep all related views of one bike/condition together.

The complete held-out set must cover the tags enforced by the runner: quality,
view type, front/rear ambiguity, occlusion and visual confounders; visible
condition categories; loose/packaging/screenshot/multiple/non-bike inputs;
prompt injection; safety insufficiency; and measurement-required cases.

## Run and review

Provide a small adapter such as `my_eval_adapter:execute` that receives an
`EvaluationRequest`. It must execute the real extractor and diagnostic flow
and return the documented result fields; it receives bytes, not storage paths
or provider output.

```bash
UV_CACHE_DIR=/tmp/bike-doc-uv-cache uv run --project apps/api python \
  evals/bike-doc/image-diagnosis/evaluate.py \
  --dataset evals/bike-doc/image-diagnosis/dataset.json \
  --executor my_eval_adapter:execute \
  --baseline evals/bike-doc/image-diagnosis/reviewed-v1.baseline.json \
  --output /tmp/bike-doc-image-diagnosis.json
```

Review the smoke results first, then use the held-out cases. `--accept` also
requires `--reviewed-by` and refuses a regressed baseline. An accepted baseline
contains provenance, all required versions, both mode reports, and the named
qualified reviewer.

Changing preprocessing, observation schema, extractor prompt/model,
diagnostic prompt/model, or image-resolution policy requires updating the
version metadata and comparing against the prior accepted baseline. A missing
baseline is explicitly required work, not a pass. Prompt-injection and
measurement-required tags have strict pass/fail metrics for both model views.

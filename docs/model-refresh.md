# Model refresh runbook

The manual loop to publish a better model. No GPU CI; training runs on
local hardware, everything else is local + releases.

## 0. Setup

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r training/requirements.txt
```

## 1. Fresh training data

- Pull the live intake from production into `data/intake/` (see
  `docs/intake-contract.md` for format; these arrive from `/report`
  and auto-saved `malformed/` solves).
- Run the labeling site on the unlabeled subset (PythonAnywhere or
  locally: `app.py`), collect 5-vote consensus labels.
- Merge into `data/captchas/` with filename == correct answer.
- `python training/verify_labels.py` (validates label integrity).

Then rebuild the clean dataset zip archives if needed:

```sh
python training/reorganize_dataset.py   # data/captchas -> data/*.zip
```

## 2. Train

```sh
python training/train.py
```

Writes `model/captcha_model.pth` + `model/metrics.json` on every
val-loss improvement. React to plateaus: the scheduler halves LR
automatically, more epochs rarely help past loss ~0.002 but more *data*
always does.

## 3. Export + parity + eval

```sh
python training/export_onnx.py     # writes model/captcha_model.onnx(+.data)
python training/parity_check.py    # MUST pass: exit != 0 blocks release
python training/decode_captchas.py # exact-match report on data/test
```

Compare `decode_captchas` OCR vs the labels in `data/test_results.txt`
against production numbers. Release only if non-regressing (see
`docs/model-spec.md`).

## 4. Ship

1. Create a release tag `model-YYYY.MM` with:
   `captcha_model.onnx`, `captcha_model.onnx.data`, `metrics.json`.
2. Bump the API: redeploy docker (fetch-model.sh + compose up), run
   `scripts/smoke.sh`.
3. Watch `/solve` failure reporting volume for a day; if the new model
   reports *more* failures, roll back by re-pinning the previous release
   tag in deployment.

## Known data pathology

- `data/test/` labels were initially produced by the model
  that trained them; treat disagreement as a label review job, not a
  model failure. When in doubt, hand-check, and route unknowns into the
  labeler rather than trusting the filename.
- Uppercase-only training artifacts: normalize filenames lowercase
  before training (they always were).

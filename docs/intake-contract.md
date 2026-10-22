# Intake contract (service -> labeler -> training)

All failed/malformed captcha images arrive in one place and are
deduplicated so the retraining loop consumes the full signal.

## Where files land

The API resolves the intake root from `REPORT_DIR` (default `./reports`,
docker image sets `/app/reports`, compose mounts `./reports` from the repo).

```
$REPORT_DIR/
  malformed/      auto-saved whenever /solve returns != 6 chars
  failed/         user-reported failures sent to /report
```

## File naming and dedupe

Filename is the SHA-256 of the raw image bytes plus the prediction:

```
<sha256>_<prediction-without-slashes>.jpg
<sha256>_<prediction-without-slashes>.json
```

- Same captcha from different users dedupes onto one filename (jpg).
- The sidecar `.json` records `ts`, `prediction` (original string),
  `source` (optional, e.g. `extension`, `android`, `labeler`) and byte
  `length`. Last writer wins for duplicates with different metadata,
  the jpg write is always a no-op retry of identical bytes.
- The sha256 prefix is the canonical dedupe key; never rename download
  a batch without preserving sha or you defeat dedupe.

## Why this exists

The previous build wrote `{ts}_{pred}.jpg` into the container CWD with no
volume; every redeploy destroyed months of failure evidence silently.
This contract plus the `./reports` volume ends that.

## Flow into the dataset

1. Operator copies `reports/**` into `data/intake/` (fresh images only;
   sha-based skip duplicating `data/captchas`).
2. Unlabeled files get 5-vote consensus labeling on the labeler site.
3. Labeled files join `data/captchas/` before the next `train.py` run.
4. `training/verify_labels.py` checks filename consistency.

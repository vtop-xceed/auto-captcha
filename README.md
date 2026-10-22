# VTOP++ AutoCaptcha

Captcha-solver backend for the VTOP login portal, plus the model pipeline
and the community labeling site that trains it.

- Rust API (`/solve`, `/report`, `/health`) + ONNX CRNN solver over a unix socket
- PyTorch (CNN + BiGRU, CTC) training pipeline with ONNX export and parity gate
- Flask labeling site live on PythonAnywhere, crowdsourced by 45+ contributors

Not affiliated with VIT Bhopal. Read `docs/deployment.md` for the live stack
and `docs/model-refresh.md` for the retraining loop.

## Layout

```
service/    Rust axum API +ORT solver (deployed image)
training/   PyTorch model, train/export/parity/labeling utilities
labeler/    Flask app for crowd labeling (live on PythonAnywhere)
workers/    Cloudflare proxies (captop-proxy -> API, captop -> labeler)
model/      Model binaries, gitignored, shipped as GitHub releases
data/       Dataset + feedback intake, gitignored
docs/       Contracts and how-to's
infra/      Where things run today
scripts/    build-image / fetch-model / smoke
```

## Quickstart

Local Rust (model expected at `model/captcha_model.onnx`):

```sh
cargo run --manifest-path service/Cargo.toml --bin solver &
cargo run --manifest-path service/Cargo.toml --bin api
scripts/smoke.sh
```

Docker:

```sh
scripts/fetch-model.sh <release-tag>   # or train one: see docs/model-refresh.md
docker compose up -d --build
scripts/smoke.sh
```

## Report intake (model retraining fuel)

Clients POST `/report` with the base64 image, prediction and optionally a
`source` string. Images land under `$REPORT_DIR/failed/` (deduplicated by
SHA-256) with a sidecar `.json`. Contract: `docs/intake-contract.md`.
The labeler reads the same `data/` tree, so failures flow straight back
into training. Mount a volume on `/app/reports` or the data is lost on
redeploy.

## Definition of done for this repo

A fresh clone builds the toolchain (`cargo test` in service), a trained
model passes `training/parity_check.py` against the ONNX export, and
`scripts/smoke.sh` returns a 6-char solve against `docker compose up`.

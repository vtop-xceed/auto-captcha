# VTOP++ AutoCaptcha

Captcha-solver backend for the VTOP login portal, plus the PyTorch
training pipeline and the community labeling site behind it.

- Rust API (`/solve`, `/health`, `/report`) + ONNX CRNN solver over a unix socket
- PyTorch (CNN + BiGRU, CTC) training pipeline with ONNX export
- Flask labeling site live on PythonAnywhere, crowdsourced by 45+ contributors

Not affiliated with VIT Bhopal.

## Layout

```
deploy/     Docker build + compose. api/ (the Rust service) and
            captcha_model.onnx drop here - the two things deploy requires
training/   Everything that feeds the model: train+export code,
            eval, labeler/ (crowd-label Flask site), data/ (dataset)
            and model/ (.pth checkpoint)
```

## Quickstart

Local Rust (model expected at `deploy/captcha_model.onnx`):

```sh
cargo run --manifest-path deploy/api/Cargo.toml --bin solver &
cargo run --manifest-path deploy/api/Cargo.toml --bin api
```

Docker:

```sh
docker compose -f deploy/docker-compose.yml up -d --build
```

## Endpoints

- `POST /solve` - body `{"image": "<base64>"}` -> `{"text": "..."}`.
  Undecodable images are saved to `reports/` and rejected with 400, capped
  so a flood cannot fill the disk.
- `GET /health` - 200 when the solver is up, 503 otherwise.
- `GET /report` - public, no auth: the API status page - uptime, counters,
  request timeline, newest-first event log, auto-refresh 5s. Images land
  in `REPORT_DIR` (default `reports/`, persistent volume in compose).
- `GET /malformed` - HTTP Basic (`AUTH_PASSWORD`). Streams a
  highly compressed zip of every saved image and then deletes them from
  the server, so each malformed batch is fetched exactly once. Monthly
  pipeline hook:
  `curl -u admin:$PASSWORD -o malformed-$(date +%F).zip https://host/malformed`
  -> relabel -> `training/train/train.py` -> rebuild image.

`AUTH_PASSWORD` comes from the runtime environment (Render dashboard /
compose `environment:`), never baked into the image. It only gates
`/malformed`; unset disables that endpoint entirely (fail-closed 404).

Train a model (the export lands in `deploy/` so the image build picks it up):

```sh
pip install -r training/requirements.txt
MODEL_PATH=deploy/captcha_model.onnx cargo run --bin solver   # from deploy/api
python training/train/train.py      # trains + exports deploy/captcha_model.onnx(+.data)
python training/decode_captchas.py
```

## Definition of done for this repo

A fresh clone builds the toolchain (`cargo test` in deploy/api), a trained
model exports to `deploy/` without error, and
`docker compose -f deploy/docker-compose.yml up -d --build` answers
6-char solves from the model baked into the image.

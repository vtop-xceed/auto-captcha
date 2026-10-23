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
  Undecodable images are saved to `reports/` and rejected with 400.
- `GET /health` - 200.
- `GET /report` - browser popup (HTTP Basic): the API status page -
  uptime, total requests, a 30-day requests/day timeline, and the last
  500 collected events, auto-refresh 5s. Images land in `REPORT_DIR`
  (default `reports/`, persistent volume in compose). An empty
  `AUTH_PASSWORD` disables the whole page (404).
- `GET /malformed` - same basic auth. Streams a highly compressed zip of
  every saved image and then deletes them from the server, so each
  malformed batch is fetched exactly once. Monthly pipeline hook:
  `curl -u admin:$PASSWORD -o malformed-$(date +%F).zip https://host/malformed`
  -> relabel -> `training/train.py` -> rebuild image.

`AUTH_PASSWORD` is baked into the Docker image via a GitHub Actions
secret (`build-args` in release.yml). Anyone who can pull the image can
read it with `docker inspect` - treat it as a trivial gate, not security.

Train a model (the export lands in `deploy/` so the image build picks it up):

```sh
pip install -r training/requirements.txt
python training/train.py           # trains + exports deploy/captcha_model.onnx(+.data)
python training/decode_captchas.py
```

## Definition of done for this repo

A fresh clone builds the toolchain (`cargo test` in deploy/api), a trained
model exports to `deploy/` without error, and
`docker compose -f deploy/docker-compose.yml up -d --build` answers
6-char solves from the model baked into the image.

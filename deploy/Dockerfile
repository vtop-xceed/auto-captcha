# Build stage (glibc: ort's download-binaries feature links the prebuilt runtime
# statically, so the runtime stage only needs libstdc++)
FROM rust:slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Build context is the repo root, not deploy/
COPY deploy/api/Cargo.toml deploy/api/Cargo.lock ./
COPY deploy/api/src ./src

RUN cargo build --release

# Runtime stage
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgcc1 libstdc++6 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy binaries
COPY --from=builder /app/target/release/api /app/api
COPY --from=builder /app/target/release/solver /app/solver

# Copy model assets (output of training/train.py, or a GitHub release)
COPY deploy/captcha_model.onnx /app/model/captcha_model.onnx
COPY deploy/captcha_model.onnx.data /app/model/captcha_model.onnx.data

# Copy entrypoint
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Default ports and paths
ENV PORT=3000
ENV MODEL_PATH=/app/model/captcha_model.onnx
# Baked at build time via --build-arg (GitHub secret). Anyone with the
# image can read it (docker inspect); it only guards /report.
ARG AUTH_PASSWORD=""
ENV AUTH_PASSWORD=${AUTH_PASSWORD}

ENTRYPOINT ["/app/entrypoint.sh"]

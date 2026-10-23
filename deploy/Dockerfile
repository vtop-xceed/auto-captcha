# Build stage
FROM alpine:latest AS builder

# Install build dependencies, rust, cargo, and onnxruntime development headers
RUN apk add --no-cache musl-dev gcc pkgconfig onnxruntime-dev g++ make rust cargo

WORKDIR /app
# Build context is the repo root, not deploy/
COPY deploy/api/Cargo.toml deploy/api/Cargo.lock ./
COPY deploy/api/src ./src

# Use system onnxruntime to avoid downloading incompatible glibc binaries
ENV ORT_STRATEGY=system
RUN cargo build --release

# Runtime stage
FROM alpine:latest

# Install onnxruntime and other runtime dependencies
RUN apk add --no-cache libgcc libstdc++ onnxruntime

WORKDIR /app

# Copy binaries
COPY --from=builder /app/target/release/api /app/api
COPY --from=builder /app/target/release/solver /app/solver

# Copy model assets (output of training/onnx.py, or a GitHub release)
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

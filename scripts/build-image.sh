#!/bin/sh
set -e
cd "$(dirname "$0")/.."

if [ ! -f model/captcha_model.onnx ] || [ ! -f model/captcha_model.onnx.data ]; then
  echo "model/captcha_model.onnx(.data) missing."
  echo "Fetch from a GitHub release: scripts/fetch-model.sh <release-tag>"
  exit 1
fi

echo "Building service image (build-only check)..."
docker build -q -f service/Dockerfile .
echo "Image build OK."

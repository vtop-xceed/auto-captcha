#!/bin/sh
set -e
cd "$(dirname "$0")/.."

mkdir -p model

TAG="${1:-latest-model}"
REPO="${AUTOCAPTCHA_REPO:-vtop-xceed/auto-captcha}"

if command -v gh >/dev/null 2>&1; then
  echo "Downloading model artifacts from release '$TAG'..."
  gh release download "$TAG" --repo "$REPO" --dir model --clobber \
    --pattern "captcha_model.onnx" --pattern "captcha_model.onnx.data"
else
  echo "gh not found. Download captcha_model.onnx and captcha_model.onnx.data"
  echo "from https://github.com/$REPO/releases/tag/$TAG into ./model/"
  exit 1
fi

echo "Model in place:"
ls -lh model/

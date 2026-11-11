#!/bin/bash
# One-time (and re-runnable) setup inside the container:
#   docker compose exec musubi musubi-setup
# Builds /work/venv and installs musubi-tuner at a pinned commit.
set -euo pipefail

MUSUBI_REPO="https://github.com/kohya-ss/musubi-tuner.git"
# Pinned so a rebuild never pulls an untested trainer. Bump on purpose.
MUSUBI_COMMIT="${MUSUBI_COMMIT:-f8a1b03794a49239a3539015075f5123d6c07d66}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"

cd /work

if [ ! -d venv ]; then
  python3.12 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip wheel

if [ ! -d musubi-tuner/.git ]; then
  git clone "$MUSUBI_REPO" musubi-tuner
fi
git -C musubi-tuner fetch --quiet origin
git -C musubi-tuner checkout --quiet "$MUSUBI_COMMIT"

pip install torch torchvision --index-url "$TORCH_INDEX"
pip install -e ./musubi-tuner
# adamw8bit needs bitsandbytes; tensorboard for loss curves.
pip install bitsandbytes tensorboard

python - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0))
PY
echo "musubi-tuner $(git -C musubi-tuner rev-parse --short HEAD) ready."

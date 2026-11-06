#!/usr/bin/env bash
# Einmaliges Setup auf CachyOS / Arch: Abhängigkeiten, Python-venv, PS2Recomp bauen.
# Aufruf aus dem Projektordner:  ./tools/setup_cachyos.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

echo "==> Systempakete (sudo)"
sudo pacman -S --needed --noconfirm base-devel git cmake ninja clang python \
    ffmpeg libxrandr libxinerama libxcursor libxi libxkbcommon mesa wayland

echo "==> Python-venv mit pycdlib + pyelftools"
python -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip pycdlib pyelftools capstone unicorn

echo "==> PS2Recomp holen (frischer Clone mit Submodulen)"
if [ ! -d "$ROOT/third_party/PS2Recomp-git/.git" ]; then
    git clone --recurse-submodules https://github.com/ran-j/PS2Recomp.git "$ROOT/third_party/PS2Recomp-git"
fi
cd "$ROOT/third_party/PS2Recomp-git"
git checkout "$(cat "$ROOT/third_party/PS2Recomp.COMMIT")" 2>/dev/null || true
git submodule update --init --recursive

echo "==> SotC-Patch für PS2Recomp anwenden (dyn. Immediates, GS-Interrupt, IOP-Heap)"
PATCH="$ROOT/patches/ps2recomp-sotc.patch"
if git apply --reverse --check "$PATCH" 2>/dev/null; then
    echo "   schon angewendet"
else
    git apply "$PATCH"
fi

echo "==> PS2Recomp bauen (Release)"
cmake -S . -B out -G Ninja -DCMAKE_BUILD_TYPE=Release
ninja -C out ps2_analyzer ps2_recomp ps2_runtime

echo
echo "Fertig. Weiter mit:  ./tools/pipeline.sh"

#!/usr/bin/env bash
# PGO-Variante (SOTC_PGO=gen|use, eigenes Ausgabeverzeichnis out_pgo, Profil in pcbuild/pgo_daten).
# Baut ps2EntryRunner nativ auf diesem PC (statt in Claudes 2-Kern-Container).
#   pcbuild/sotc_src.tar.xz  -> komplette Quellen (Erstinstallation)
#   pcbuild/patch_*.tar.xz   -> spätere Änderungen (nur geänderte Dateien), werden der Reihe nach eingespielt
#   Ergebnis: build/sotc/ps2EntryRunner_<NAME> (NAME = 1. Argument, Standard "pc")
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
B="$ROOT/pcbuild"; NAME="${1:-pc}"; JOBS="${SOTC_BAU_JOBS:-10}"
cd "$B"
exec 6>"$B/.baulock"; flock 6   # nie zwei Builds gleichzeitig im selben Baum
if [ ! -d src/sotc-game ] && [ -f sotc_src.tar.xz ]; then echo "== Quellen entpacken"; tar xJf sotc_src.tar.xz -C src; fi
for p in $(ls -1 patch_*.tar.xz 2>/dev/null | sort); do
    [ -f "eingespielt/$p" ] && continue
    echo "== Patch $p einspielen"; tar xJmf "$p" -C src; mkdir -p eingespielt; cp "$p" "eingespielt/$p"
done
SRC="$B/src/sotc-game"; OUT="$B/out_pgo"; PGODIR="$HOME/.cache/sotc_pgo"; mkdir -p "$PGODIR"; PHASE="${SOTC_PGO:-gen}"
if [ "$PHASE" = gen ]; then PGOF="-DSOTC_PGO_GEN -fprofile-generate -fprofile-update=prefer-atomic -fprofile-dir=$PGODIR"; else PGOF="-fprofile-use -fprofile-correction -fprofile-partial-training -Wno-missing-profile -Wno-coverage-mismatch -fprofile-dir=$PGODIR"; fi
echo "== Konfigurieren"
{
    cmake -S "$SRC" -B "$OUT" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DCMAKE_C_COMPILER=${SOTC_CC:-gcc} -DCMAKE_CXX_COMPILER=${SOTC_CXX:-g++} \
        -DCMAKE_C_FLAGS="-msse4.2 -mavx -mavx2 $PGOF" -DCMAKE_CXX_FLAGS="-msse4.2 -mavx -mavx2 $PGOF" -DCMAKE_EXE_LINKER_FLAGS="$PGOF" \
        -DCMAKE_LINKER_TYPE=LLD \
        -DPS2X_BUILD_RECOMP=OFF -DPS2X_BUILD_ANALYZER=OFF -DPS2X_BUILD_TEST=OFF -DPS2X_BUILD_STUDIO=OFF \
        -DPS2X_ENABLE_DEBUG_UI=OFF -DPS2X_ENABLE_AGRESSIVE_LOGS=OFF -DPS2X_ENABLE_SCCACHE=OFF \
        -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="-O2 -g0" -DCMAKE_C_FLAGS_RELWITHDEBINFO="-O2 -g -DNDEBUG" \
        -DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE=${SOTC_UNITY:-48} > "$B/konfig.log" 2>&1 || { tail -30 "$B/konfig.log"; exit 1; }
}
echo "== Bauen mit $JOBS Jobs"; start=$(date +%s)
nice -n 10 cmake --build "$OUT" -j"$JOBS" --target ps2EntryRunner
echo "== fertig nach $(( $(date +%s) - start )) s"
BIN=$(find "$OUT" -name ps2EntryRunner -type f -perm -u+x | head -1)
mkdir -p "$ROOT/build/sotc/symbole"; nm -C -S --defined-only "$BIN" > "$ROOT/build/sotc/symbole/ps2EntryRunner_$NAME.nm" 2>/dev/null || true  # Symboltabelle fuer Spielprofile
strip -o "$ROOT/build/sotc/ps2EntryRunner_$NAME.tmp" "$BIN" && mv "$ROOT/build/sotc/ps2EntryRunner_$NAME.tmp" "$ROOT/build/sotc/ps2EntryRunner_$NAME"
ls -l "$ROOT/build/sotc/ps2EntryRunner_$NAME"; sha256sum "$ROOT/build/sotc/ps2EntryRunner_$NAME" | cut -c1-12

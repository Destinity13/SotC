#!/usr/bin/env bash
# Baut die native SotC-Binary: generierter Code (recomp/output) + SotC-Override + ps2xRuntime.
# Aufruf aus dem Projektordner:  ./tools/build_game.sh    (Ergebnis: build/sotc/ps2EntryRunner)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SRC="${SOTC_SRC:-$ROOT/recomp}"
G="$ROOT/build/sotc-game"
JOBS="${JOBS:-$(nproc)}"
mkdir -p "$G"
# eigener Quellbaum für das Spiel (Kopie von PS2Recomp, ohne dessen Build-Ordner)
rsync -a --delete --exclude out --exclude .git --exclude 'ps2xRuntime/src/runner/*.cpp' "$SRC/" "$G/"
RUN="$G/ps2xRuntime/src/runner"
rm -f "$RUN"/*.cpp
cp "$ROOT"/recomp/output/*.cpp "$RUN/"
cp "$ROOT"/recomp/output/*.h "$G/ps2xRuntime/include/"
cp "$ROOT/recomp/ps2xRuntime/src/runner/sotc_override.cpp" "$RUN/"
# generierte Fassungen der Funktionen entfernen, die der Override selbst definiert
for f in $(grep -oE '^SOTC_DEFINE\((\w+)' "$ROOT/recomp/ps2xRuntime/src/runner/sotc_override.cpp" | sed 's/SOTC_DEFINE(//'); do
    echo "// replaced by sotc_override.cpp" > "$RUN/$f.cpp"
done
# Riesige Funktionstabelle nicht optimieren (sonst geht dem Compiler der Speicher aus)
grep -q "register_functions.cpp\"" "$G/ps2xRuntime/CMakeLists.txt" || python3 - "$G/ps2xRuntime/CMakeLists.txt" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
s=s.replace("if(PS2X_ENABLE_RUNNER_UNITY_BUILD)\n", '''if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/src/runner/register_functions.cpp")
    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/src/runner/register_functions.cpp"
        PROPERTIES SKIP_UNITY_BUILD_INCLUSION ON COMPILE_OPTIONS "-O0")
endif()

if(PS2X_ENABLE_RUNNER_UNITY_BUILD)
''', 1)
open(p,'w').write(s)
PY
cmake -S "$G" -B "$G/out" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo "-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=-O2 -g0" \
    -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF -DPS2X_BUILD_RECOMP=OFF -DPS2X_ENABLE_AGRESSIVE_LOGS=OFF \
    -DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE=128 -DCMAKE_CXX_FLAGS="-march=native" -DCMAKE_C_FLAGS="-march=native"
ninja -C "$G/out" -j "$JOBS" ps2EntryRunner
mkdir -p "$ROOT/build/sotc"
cp "$G/out/ps2xRuntime/ps2EntryRunner" "$ROOT/build/sotc/"
echo "Fertig: $ROOT/build/sotc/ps2EntryRunner"

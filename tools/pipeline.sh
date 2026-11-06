#!/usr/bin/env bash
# Komplette Pipeline: ISO -> Dateien -> XFF->ELF -> Daten-Index -> statisch linken -> PS2Recomp-C++.
# Teure Schritte überspringen sich, wenn ihr Ergebnis schon existiert (FORCE=1 erzwingt alles).
# Danach: ./tools/build_game.sh (native Binary) und ./tools/run_game.sh (starten).
# Aufruf aus dem Projektordner:  ./tools/pipeline.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
ISO="${SOTC_ISO:-$ROOT/../Shadow of the Colossus (Europe, Australia) (En,Fr,De,Es,It).iso}"
DECOMP="${SOTC_DECOMP:-$ROOT/third_party/sotc-decomp}"
RECOMP="${SOTC_RECOMP_OUT:-$ROOT/third_party/PS2Recomp-git/out}"
RUNTIME_SRC="${SOTC_RUNTIME_SRC:-$ROOT/recomp/ps2xRuntime}"
FORCE="${FORCE:-0}"
need() { [ "$FORCE" = 1 ] || [ ! -e "$1" ]; }

echo "==> 1/8 ISO extrahieren"
"$PY" "$ROOT/tools/extract_iso.py" "$ISO" "$ROOT/extracted" | tail -1

echo "==> 2/8 Prüfsummen (PAL SCES-53326)"
sha1sum -c --quiet "$ROOT/docs/sha1sums.txt" && echo "OK"

echo "==> 3/8 XFF -> ELF"
for m in KERNEL MANAGER GAMECORE; do
    if need "$ROOT/elf/$m.o"; then
        "$PY" "$DECOMP/tools/xff2.py" roundtrip "$ROOT/extracted/$m.XFF"
        "$PY" "$DECOMP/tools/xff2.py" to-elf "$ROOT/extracted/$m.XFF" "$ROOT/elf/$m.o"
    fi
done

echo "==> 4/8 Datentabellen im Archiv indizieren (NICO.DAT + XAB..XAE)"
if need "$ROOT/data_xff/index.json"; then
    "$PY" "$ROOT/tools/scan_archive_xff.py" "$ROOT/extracted" "$ROOT/data_xff"
fi

echo "==> 5/8 Statisch linken auf die echten Ladeadressen (docs/placement.json)"
"$PY" "$ROOT/tools/link_sotc.py" --loader "$ROOT/extracted/SCES_533.26" \
    --module "$ROOT/elf/KERNEL.o" --module "$ROOT/elf/MANAGER.o" --module "$ROOT/elf/GAMECORE.o" \
    --placement "$ROOT/docs/placement.json" \
    --extra-func GAMECORE:.text:0x4d348:effectSetDamageWork_tail_4d348 \
    --out "$ROOT/elf/sotc_linked.elf" --layout "$ROOT/elf/sotc_layout.json" \
    --dynamic-sites "$ROOT/elf/sotc_dynamic_sites.json" --symbol-owners "$ROOT/data_xff/index.json" \
    --code-pointers "$ROOT/elf/code_pointers.txt"

echo "==> 6/8 PS2Recomp-Config erzeugen"
cd "$ROOT/recomp"
"$RECOMP/ps2xAnalyzer/ps2_analyzer" ../elf/sotc_linked.elf sotc_config.toml > analyzer.log 2>&1
sed -i 's|^single_file_output *=.*|single_file_output = false|' sotc_config.toml
# Stubs nur behalten, wenn die Runtime sie wirklich implementiert und der Name zum Symbol passt
"$PY" "$ROOT/tools/filter_stubs.py" sotc_config.toml "$RUNTIME_SRC" ../elf/sotc_linked.elf
# Einsprungpunkte: Sprung mitten in KERNEL-Code + alle Funktionszeiger aus den Relocations
"$PY" - <<'PY'
t = open('sotc_config.toml').read()
ptrs = [l.strip() for l in open('../elf/code_pointers.txt') if l.strip()]
extra = ['0x001B99B0'] + ptrs
t = t.replace('entry_points = [\n', 'entry_points = [\n' + ''.join(f'  "{p}",\n' for p in extra), 1)
open('sotc_config.toml', 'w').write(t)
print(f'{len(extra)} zusätzliche Einsprungpunkte')
PY
"$PY" "$ROOT/tools/gen_dyn_toml.py" sotc_config.toml ../elf/sotc_dynamic_sites.json

echo "==> 7/8 PS2Recomp (Durchgang 1) + fehlende Sprungziele"
rm -rf output
"$RECOMP/ps2xRecomp/ps2_recomp" sotc_config.toml > recomp.log 2>&1
"$PY" "$ROOT/tools/find_missing_targets.py" ../elf/sotc_linked.elf output/register_functions.cpp sotc_config.toml

echo "==> 8/8 PS2Recomp (Durchgang 2)"
rm -rf output
"$RECOMP/ps2xRecomp/ps2_recomp" sotc_config.toml > recomp.log 2>&1
tail -1 recomp.log
echo "C++-Dateien: $(ls output/*.cpp | wc -l)  ->  $ROOT/recomp/output"
echo "Weiter mit: ./tools/build_game.sh"

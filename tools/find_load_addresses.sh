#!/usr/bin/env bash
# Ermittelt die echten Ladeadressen von KERNEL/MANAGER/GAMECORE, indem der Original-Loader-
# und KERNEL-Code des Spiels unter Unicorn (MIPS64 + R5900-Erweiterungen) ausgeführt wird.
# Ergebnis: docs/placement.json (ist schon dabei – nur nötig, wenn du etwas am Ablauf änderst).
# Dauer: ca. 10–15 Minuten.  Aufruf aus dem Projektordner: ./tools/find_load_addresses.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="$PWD/.venv/bin/python"; [ -x "$PY" ] || PY=python3
mkdir -p work/emu
"$PY" tools/link_sotc.py --loader extracted/SCES_533.26 --module elf/KERNEL.o --module elf/MANAGER.o \
    --module elf/GAMECORE.o --placement docs/placement.json --out work/emu/linked.elf --layout work/emu/layout.json
"$PY" tools/emu_loader.py extracted work/emu/linked.elf work/emu/boot.json \
    --kernel "$(python3 -c "import json;s=json.load(open('docs/placement.json'))['KERNEL']['sections']['.text'];print(s['addr']+':'+s['size'])")" \
    --dump work/emu/mem.bin | tee work/emu/boot.log | grep -E "^==|krnl|ld:"
echo "Selbsttest (gelinkte ELF vs. vom Spiel selbst geladener Speicher):"
"$PY" tools/link_sotc.py --loader extracted/SCES_533.26 --module elf/KERNEL.o --module elf/MANAGER.o \
    --module elf/GAMECORE.o --placement docs/placement.json --out work/emu/check.elf --layout work/emu/check.json \
    --dynamic-sites work/emu/check_sites.json >/dev/null
"$PY" tools/compare_mem2.py work/emu/check.elf work/emu/mem.bin work/emu/check_sites.json | tail -1

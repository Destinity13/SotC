#!/usr/bin/env bash
# SotC-Testläufe auf dem eigenen PC: mehrere Szenarien gleichzeitig, automatisch, mit Screenshots.
# Aufruf aus dem Projektordner:
#   ./tools/sotc_bench.sh              alle Szenarien (so viele parallel, wie CPU und RAM hergeben)
#   ./tools/sotc_bench.sh schrein      nur ein Szenario
#   SOTC_JOBS=4 ./tools/sotc_bench.sh  Anzahl gleichzeitiger Läufe selbst festlegen
# Ergebnisse: build/tests/<szenario>/{log.txt,shot_*.ppm,summary.txt} und build/tests/ERGEBNIS.txt
# Danach Claude Bescheid geben, die Ergebnisse werden von dort abgeholt.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
RUN="$ROOT/build/run"
BIN="$ROOT/build/sotc/ps2EntryRunner"
ISO="${SOTC_ISO:-$ROOT/../Shadow of the Colossus (Europe, Australia) (En,Fr,De,Es,It).iso}"
TESTS="${SOTC_TESTS_DIR:-$ROOT/build/tests}"
# Vorlauf beschleunigen: VU1 rechnet nicht, solange nicht gezeichnet wird (Ergebnis bitgleich geprüft)
export PS2X_MTGS="${PS2X_MTGS:-1}"   # GS-Rasterizer im eigenen Thread (+56 %); "0" = synchron
export PS2X_VU1_FAST="${PS2X_VU1_FAST:-1}"   # VU1 ohne long-double-Nachrechnung (bildgleich, ~7 % schneller)
export PS2X_MTVU="${PS2X_MTVU:-1}"         # VIF1+VU1 auf eigenem Thread (wie PCSX2 MTVU)
export PS2X_VU1_REC="${PS2X_VU1_REC:-1}"   # vorab uebersetzte VU1-Programme
# Testlaeufe immer mit dem Referenz-Texturstand (9 Seiten Kantenformung, wie bei den pc126-Referenzbildern), nie mit dem HD-Text aus ~/.config
export PS2XGS_TEXDIR="${PS2XGS_TEXDIR-$ROOT/build/texturen}"
export PS2X_VBLANK_HZ="${PS2X_VBLANK_HZ:-50}"   # PAL SCES-53326: 50 Hz wie PS2/PCSX2 (Partikel-Zeitschritt bitgleich 0.0196608); "60" fuer NTSC-Takt
export SOTC_SKIP_VU1_NODRAW="${SOTC_SKIP_VU1_NODRAW:-1}"
# EE-FPU wie PCSX2-Standard: Richtung null runden + Denormale als null (FPUFPCR-Default). "aus" = Wirtsstandard.
export PS2X_EE_ROUND="${PS2X_EE_ROUND:-chop_daz}"; [ "$PS2X_EE_ROUND" = aus ] && unset PS2X_EE_ROUND

# Laufordner wie in run_game.sh vorbereiten
mkdir -p "$RUN" "$TESTS"
for f in "$ROOT"/extracted/*; do
    name="$(basename "$f")"
    [ "$name" = "SCES_533.26" ] && continue
    ln -sfn "$f" "$RUN/$name"
done
cp -f "$ROOT/elf/sotc_linked.elf" "$RUN/SCES_533.26"
# Binary neu entpacken, wenn die .xz neuer ist (sonst läuft eine alte Version)
if [ -f "$BIN.xz" ] && { [ ! -x "$BIN" ] || [ "$BIN.xz" -nt "$BIN" ]; }; then
    echo "==> Entpacke aktuelle Binary"
    xz -dkf "$BIN.xz" && chmod +x "$BIN" && touch "$BIN"
fi
# Ältere Systeme (zu altes glibc): mitgelieferte Bibliotheken benutzen
LAUNCH=()
if ldd "$BIN" 2>&1 | grep -q "not found"; then
    RT="$ROOT/build/sotc/rtlibs"
    [ -d "$RT" ] || tar xJf "$ROOT/build/sotc/rtlibs.tar.xz" -C "$ROOT/build/sotc"
    LAUNCH=("$RT/ld-linux-x86-64.so.2" --library-path "$RT")
    echo "==> benutze mitgelieferte Bibliotheken"
fi
[ -f "$ISO" ] || { echo "ISO nicht gefunden: $ISO"; exit 1; }

# Gemeinsamer Weg bis ins Spiel (Sprache, 50 Hz, Titel, New Game, Intro überspringen)
P="330:x,480:x,930:start,1250:start,1450:x,2700:start,2760:x"
# Szenario: name|eingaben|screenshot-bilder|ende-bild|zeichnen-überspringen-bis
SCENARIOS=(
  "titel|330:x,480:x,930:start|700,900,1100,1250|1260|600"
  "titel_optionen|330:x,480:x,930:start,1250:down,1300:down,1350:x,1450:x|1250,1320,1400,1500,1600|1610|1200"
  "intro|330:x,480:x,930:start,1250:start,1450:x|1450,1650,1900,2200|2210|1400"
  "schrein|$P,2950:lup*80,3040:rleft*40|2800,2950,3000,3040,3090,3200|3210|2790"
  "laufen|$P,3100:lup*300,3450:x,3500:lup*300|3100,3300,3450,3600,3800|3810|3090"
  "kamera|$P,2900:rleft*60,3000:rright*120,3150:rup*40,3220:rdown*40|2900,2960,3060,3150,3200,3260|3270|2890"
  "springen|$P,2900:lup*60,2970:t,3000:lup*40,3050:t,3100:t|2900,2972,2980,3052,3102,3140|3150|2890"
  "schwert|$P,2900:o,2950:s*150,3150:o,3200:s*60|2900,2960,3050,3140,3160,3250|3260|2890"
  "pausemenue|$P,2900:start,2960:down,3000:down,3040:x,3120:o,3180:o,3240:start|2900,2965,3005,3060,3130,3200,3260|3270|2890"
  "weit_laufen|$P,2900:rleft*90,3000:lup*1500|3000,3300,3700,4100,4500|4510|2890"
)

# Standard: kleine Fenster nebeneinander zum Zuschauen.
# SOTC_HEADLESS=1 -> unsichtbar über Xvfb (falls installiert).
# Fenstergröße/Raster: SOTC_TILE_W (Standard 480), SOTC_SCREEN_W (Standard 1920)
DISP=""
if [ "${SOTC_HEADLESS:-0}" = 1 ] && command -v Xvfb >/dev/null; then
    DISP=":97"
    pgrep -f "Xvfb $DISP " >/dev/null || { Xvfb $DISP -screen 0 640x480x24 &>/dev/null & sleep 2; }
fi
TILE_W=${SOTC_TILE_W:-320}; TILE_H=$(( TILE_W * 448 / 640 + 30 ))
COLS=$(( ${SOTC_SCREEN_W:-1920} / TILE_W )); (( COLS < 1 )) && COLS=1

# Wie viele Läufe gleichzeitig?
cores=$(nproc)
memgb=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
# Möglichst viele Läufe: alle Threads bis auf 4 (damit der PC nutzbar bleibt), ~0,7 GB RAM pro Lauf
auto=$(( cores - 4 )); byram=$(( memgb * 3 / 2 ))
(( byram < auto )) && auto=$byram
(( auto < 1 )) && auto=1
JOBS=${SOTC_JOBS:-$auto}
SLOTDIR="${XDG_RUNTIME_DIR:-/tmp}/sotc_slots"; mkdir -p "$SLOTDIR"
echo "==> $cores CPU-Threads, ${memgb} GB RAM frei -> $JOBS Läufe gleichzeitig (Plätze gemeinsam für alle Aufträge)"

run_one() {
    IFS='|' read -r name input shots exit skip <<<"$1"
    # Globale Plätze (gelten über alle gleichzeitig laufenden Aufträge hinweg)
    local slot=-1 fd
    while (( slot < 0 )); do
        for ((i=0; i<JOBS-${SOTC_SLOT_RESERVE:-0}; i++)); do
            exec {fd}>"$SLOTDIR/slot_$i"
            if flock -n "$fd"; then slot=$i; break; fi
            exec {fd}>&-
        done
        (( slot < 0 )) && sleep 5
    done
    out="$TESTS/$name"
    [ -d "$out" ] && mv "$out" "$out.alt.$(date +%s)"
    mkdir -p "$out"
    # Spielstand vorgeben: Speicherkarten-Vorlage (z. B. saves/karte_a.ps2) in den Testordner kopieren
    if [ -n "${SOTC_MEMCARD_TEMPLATE:-}" ]; then
        tpl="$SOTC_MEMCARD_TEMPLATE"; [ "${tpl#/}" = "$tpl" ] && tpl="$ROOT/$tpl"
        cp -f "$tpl" "$out/Mcd001.ps2"
    fi
    RUNBIN="$BIN"; [ -n "${SOTC_BIN_FILE:-}" ] && RUNBIN="$ROOT/build/sotc/$SOTC_BIN_FILE"
    # Programm wird evtl. gerade gebaut (pc_bauen.sh hält pcbuild/.baulock): bis 15 min warten statt rc=127
    for _w in $(seq 1 180); do [ -x "$RUNBIN" ] && break; sleep 5; done
    start=$(date +%s)
    ( cd "$RUN" && env ${DISP:+DISPLAY=$DISP} SOTC_ISO="$ISO" PS2X_NO_PACING="${SOTC_TEST_NO_PACING:-1}" \
        SOTC_INPUT="$input" SOTC_SHOT_TICKS="$shots" SOTC_EXIT_TICK="$exit" SOTC_SHOT_DIR="$out" \
        SOTC_SKIP_DRAW_UNTIL="$skip" SOTC_WATCHDOG=60 SOTC_DEBUG_DIR="$out" SOTC_MEMCARD="$out/Mcd001.ps2" \
        PS2X_WINDOW_SIZE="$TILE_W,$(( TILE_W * 448 / 640 ))" \
        PS2X_WINDOW_POS="$(( (slot % COLS) * TILE_W )),$(( (slot / COLS) * TILE_H + 30 ))" \
        PS2X_WINDOW_TITLE="SotC Test: $name" \
        PS2XGS_SURFACELESS="${PS2XGS_SURFACELESS:-1}" PS2X_HIDE_WINDOW="${PS2X_HIDE_WINDOW:-1}" \
        timeout --kill-after=15 3600 nice -n 10 ${SOTC_WRAP:-} "${LAUNCH[@]}" "$RUNBIN" "$RUN/SCES_533.26" > "$out/log.txt" 2>&1 )
    rc=$?
    # Sicherheitsnetz: kein Testprozess darf nach dem Lauf weiterleben (haengende Fenster)
    for p in $(pgrep -f "$RUN/SCES_533.26" 2>/dev/null); do
        tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -qx "SOTC_DEBUG_DIR=$out" && kill -9 "$p" 2>/dev/null
    done
    wall=$(( $(date +%s) - start ))
    shotsn=$(ls "$out"/shot_*.p[np][mg] 2>/dev/null | wc -l)
    want=$(tr ',' '\n' <<<"$shots" | wc -l)
    # Stabilität: Abstürze/Asserts des Spiels und Speicherstand (falls SOTC_TRACE_OOM aktiv) in die Zusammenfassung
    ab=$(grep -a -o 'aborts=[0-9]*' "$out/log.txt" | tail -1); as=$(grep -a -o 'asserts=[0-9]*' "$out/log.txt" | tail -1)
    mem=$(grep -a -o 'offen=[0-9]* Bloecke [0-9.]* MB' "$out/log.txt" | tail -1 | awk '{print $3"MB"}')
    printf '%-15s rc=%-3s %5ss  screenshots %s/%s  %s %s %s\n' "$name" "$rc" "$wall" "$shotsn" "$want" "${ab:-aborts=?}" "${as:-asserts=?}" "${mem:+speicher=$mem}" | tee "$out/summary.txt" >> "$TESTS/.runde"; cat "$out/summary.txt"
    exec {fd}>&-
}

# Auswahl: Namen als Argumente, oder SOTC_SCENARIO_FILE mit Namen und/oder eigenen Zeilen
# (Format eigener Zeilen: name|eingaben|screenshots|ende|überspringen-bis)
RUNLIST=()
sel=("$@")
if [ -n "${SOTC_SCENARIO_FILE:-}" ] && [ -f "$SOTC_SCENARIO_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        case "$line" in
            alle) sel=() ; RUNLIST+=("${SCENARIOS[@]}") ;;
            *"|"*) RUNLIST+=("$line") ;;
            *=*) export "$line" ;;
            *) sel+=("$line") ;;
        esac
    done < "$SOTC_SCENARIO_FILE"
fi
if [ ${#sel[@]} -gt 0 ]; then
    for s in "${SCENARIOS[@]}"; do for w in "${sel[@]}"; do [ "${s%%|*}" = "$w" ] && RUNLIST+=("$s"); done; done
elif [ ${#RUNLIST[@]} -eq 0 ]; then
    RUNLIST=("${SCENARIOS[@]}")
fi
JOBS=${SOTC_JOBS:-$JOBS}
t0=$(date +%s)
: > "$TESTS/.runde"
idx=0
running=0
for s in "${RUNLIST[@]}"; do
    n="${s%%|*}"
    echo "==> eingereiht: $n"
    run_one "$s" &
done
wait
total=$(( $(date +%s) - t0 ))
{ echo "Testrunde vom $(date '+%d.%m.%Y %H:%M'), $JOBS parallel, gesamt ${total}s"; sort "$TESTS/.runde"; } > "$TESTS/ERGEBNIS.txt"
echo; cat "$TESTS/ERGEBNIS.txt"
echo "==> fertig. Sag Claude Bescheid."

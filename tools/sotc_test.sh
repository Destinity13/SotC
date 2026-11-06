#!/usr/bin/env bash
# Reproduzierbarer SotC-Testlauf mit Checkpoints.
#   sotc_test.sh <name> [display] [extra env ...]
# Ergebnis: /tmp/sotc_tests/<name>/{log.txt,shot_*.ppm,contact.png,summary.txt}
# Vergleich mit /tmp/sotc_tests/baseline/ falls vorhanden.
set -u
NAME=${1:?name}; DISP=${2:-77}; shift 2 || shift $#
OUT=/tmp/sotc_tests/$NAME
rm -rf "$OUT"; mkdir -p "$OUT"
BIN=${SOTC_BIN:-$HOME/build/sotc-game/out/ps2xRuntime/ps2EntryRunner}
ELF=$HOME/run/SCES_533.26
ISO=/root/run/sotc_full.iso
INPUT=${SOTC_TEST_INPUT:-330:x,480:x,930:start,1250:start,1450:x}
SHOTS=${SOTC_TEST_SHOTS:-200,400,700,900,1100,1300,1450,1700,2000,2300}
EXIT=${SOTC_TEST_EXIT:-2400}
LIMIT=${SOTC_TEST_TIMEOUT:-900}

pgrep -f "Xvfb :$DISP " >/dev/null || { setsid Xvfb :$DISP -screen 0 640x480x24 &>/dev/null & sleep 2; }
cd "$HOME/run"
start=$(date +%s)
env DISPLAY=:$DISP SOTC_ISO=$ISO PS2X_NO_PACING=1 SOTC_INPUT=$INPUT SOTC_SHOT_TICKS=$SHOTS SOTC_EXIT_TICK=$EXIT \
    SOTC_SHOT_DIR=$OUT ${SOTC_TEST_SKIP:+SOTC_SKIP_DRAW_UNTIL=$SOTC_TEST_SKIP} SOTC_TRACE_VIF=1 SOTC_TRACE_VIFCHAIN=1 SOTC_WATCHDOG=${SOTC_TEST_WATCHDOG:-60} SOTC_MEMCARD=$OUT/Mcd001.ps2 "$@" \
    timeout $LIMIT "$BIN" "$ELF" > "$OUT/log.txt" 2>&1
rc=$?
end=$(date +%s)
python3 -I /home/claude/tools/sotc_test_report.py "$OUT" "$rc" "$((end-start))"

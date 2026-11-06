#!/usr/bin/env bash
# SotC-Testhelfer: läuft im Hintergrund und arbeitet Testaufträge von Claude ab.
#   v9: Experiment-Serien (tools/matrix.sh) werden nach der letzten Variante automatisch ausgewertet.
#   v8: Befehls-Aufträge (erste Zeile "# TYP: befehl") laufen als Skript, z. B. zum Bauen.
#   Aufträge:   queue/neu/*.job      (legt Claude an, ältester zuerst)
#   in Arbeit:  queue/laeuft/
#   erledigt:   queue/fertig/
#   Ergebnisse: build/tests/<auftrag>/  plus build/tests/<auftrag>/FERTIG
#   Status:     build/tests/STATUS.txt (wird alle 30 s aktualisiert)
# Bis zu 3 Aufträge laufen gleichzeitig; die einzelnen Testläufe teilen sich
# gemeinsame Plätze (siehe sotc_bench.sh), der PC wird also nie überlastet.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
Q="$ROOT/queue"
T="$ROOT/build/tests"
MAXJOBS=${SOTC_MAX_AUFTRAEGE:-12}
mkdir -p "$Q/neu" "$Q/laeuft" "$Q/fertig" "$Q/dauer" "$T"

# Nur eine Instanz
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/sotc_worker.lock"
flock -n 9 || { echo "Testhelfer läuft schon."; exit 0; }

# Abgebrochene Aufträge (z. B. nach Neustart) wieder einreihen – aber nur solche,
# an denen gerade niemand mehr arbeitet (jeder laufende Auftrag hält eine Sperre).
for j in "$Q/laeuft"/*.job; do
    [ -e "$j" ] || continue
    exec 8>"$j.lock"
    if flock -n 8; then mv "$j" "$Q/neu/"; rm -f "$j.lock"; fi
    exec 8>&-
done
SELF="$ROOT/tools/sotc_worker.sh"
SELF_MTIME=$(stat -c %Y "$SELF")

run_job() {
    local id=$1 out="$T/$1"
    exec 9>&-
    exec 7>"$Q/laeuft/$id.job.lock"; flock 7
    [ -d "$out" ] && mv "$out" "$out.alt.$(date +%s)"
    mkdir -p "$out"
    local rc
    if head -1 "$Q/laeuft/$id.job" | grep -q '^# TYP: befehl'; then
        # Befehls-Auftrag (z. B. Bauen, Werkzeug-Check): Skript im Projektordner nativ ausführen
        ( cd "$ROOT" && SOTC_OUT="$out" nice -n 5 bash "$Q/laeuft/$id.job" ) > "$out/ablauf.txt" 2>&1
        rc=$?
    else
        SOTC_SCENARIO_FILE="$Q/laeuft/$id.job" SOTC_TESTS_DIR="$out" "$ROOT/tools/sotc_bench.sh" > "$out/ablauf.txt" 2>&1
        rc=$?
    fi
    mv "$Q/laeuft/$id.job" "$Q/fertig/$id.job"; rm -f "$Q/laeuft/$id.job.lock"
    echo "rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" > "$out/FERTIG"
    # Experiment-Serie (M_<serie>__<variante>): nach der letzten Variante automatisch auswerten
    if [[ "$id" == M_*__* ]]; then
        local serie=${id#M_}; serie=${serie%%__*}
        if ! ls "$Q/neu"/M_${serie}__*.job "$Q/laeuft"/M_${serie}__*.job >/dev/null 2>&1; then
            "$ROOT/tools/auswerten.sh" "$serie" > /dev/null 2>&1
            command -v notify-send >/dev/null && notify-send "SotC-Serie ausgewertet" "Serie $serie fertig." 2>/dev/null || true
        fi
    fi
    command -v notify-send >/dev/null && notify-send "SotC-Test fertig" "Auftrag $id ist durch." 2>/dev/null || true
}

while true; do
    running=$(ls -1 "$Q/laeuft"/*.job 2>/dev/null | wc -l)
    if (( running < MAXJOBS )); then
        job=$(ls -1 "$Q/neu"/*.job 2>/dev/null | sort | head -1)
        # Leerlauf? Dann einen Dauer-Auftrag (Lückenfüller) einreihen – nur einen gleichzeitig.
        nfill=0; for f in "$Q/laeuft"/fueller_*.job; do [ -e "$f" ] && nfill=$((nfill+1)); done
        if [ -z "$job" ] && (( nfill < ${SOTC_MAX_FUELLER:-8} )); then
            src=$(ls -1 "$Q/dauer"/*.job 2>/dev/null | shuf -n1)
            if [ -n "$src" ]; then
                job="$Q/neu/fueller_$(date +%m%d_%H%M%S)_$(basename "$src")"
                { echo "SOTC_SLOT_RESERVE=2"; cat "$src"; } > "$job"
            fi
        fi
        if [ -n "$job" ]; then
            id=$(basename "$job" .job)
            mv "$job" "$Q/laeuft/$id.job"
            run_job "$id" &
            sleep 2
            continue
        fi
    fi
    list=""; for j in "$Q/laeuft"/*.job; do [ -e "$j" ] && list="${list:+$list,}$(basename "$j" .job)"; done
    wait_n=$(ls -1 "$Q/neu"/*.job 2>/dev/null | wc -l)
    if [ -n "$list" ]; then
        printf '%s | %s | arbeitet: %s | wartend: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$(hostname)" "$list" "$wait_n" > "$T/STATUS.txt"
    else
        printf '%s | %s | wartet auf Aufträge\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$(hostname)" > "$T/STATUS.txt"
    fi
    # Selbst-Update: neue Version des Helfers übernehmen (laufende Aufträge laufen weiter)
    if [ "$(stat -c %Y "$SELF" 2>/dev/null)" != "$SELF_MTIME" ]; then
        exec 9>&-
        exec bash "$SELF"   # über bash starten: klappt auch, wenn das Ausführungsrecht fehlt
    fi
    sleep 30
done

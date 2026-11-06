#!/usr/bin/env bash
# Live-Anzeige ALLER laufenden SotC-Tests im Terminal.
#   ./tools/sotc_monitor.sh      aktualisiert alle 5 s (Strg+C zum Beenden)
cd "$(dirname "$0")/.."
ROOT="$PWD"; T="$ROOT/build/tests"; Q="$ROOT/queue"
bar() { local p=$1 w=25 f s=""; f=$(( p * w / 100 )); for ((i=0;i<w;i++)); do if ((i<f)); then s+="#"; else s+="."; fi; done; printf '[%s]' "$s"; }
while true; do
    out=""
    aktiv=0; gesamt=0
    for jf in "$Q/laeuft"/*.job; do
        [ -e "$jf" ] || continue
        job=$(basename "$jf" .job); D="$T/$job"
        out+=$'\n'"Auftrag: $job"$'\n'
        for d in "$D"/*/; do
            [ -f "$d/log.txt" ] || continue
            n=$(basename "$d"); gesamt=$((gesamt+1))
            line=$(grep -h -m1 "^$n|" "$jf" 2>/dev/null)
            [ -z "$line" ] && line=$(grep -m1 "^  \"$n|" "$ROOT/tools/sotc_bench.sh")
            ziel=$(cut -d'|' -f4 <<<"$line" | tr -dc 0-9); skip=$(cut -d'|' -f5 <<<"$line" | tr -dc 0-9)
            tick=$(grep -a -o 'vsyncTick=[0-9]*' "$d/log.txt" | tail -1 | cut -d= -f2)
            tick=${tick:-0}; ziel=${ziel:-0}; skip=${skip:-0}
            shots=$(ls "$d"/shot_*.ppm 2>/dev/null | wc -l)
            soll=$(cut -d'|' -f3 <<<"$line" | tr ',' '\n' | grep -c .)
            if [ -f "$d/summary.txt" ]; then phase="fertig"; tick=$ziel
            elif (( tick < skip )); then phase="Vorlauf (schnell, Fenster schwarz)"; aktiv=$((aktiv+1))
            else phase="zeichnet + Fotos"; aktiv=$((aktiv+1)); fi
            p=0; (( ziel > 0 )) && p=$(( tick * 100 / ziel )); (( p > 100 )) && p=100
            out+=$(printf '  %-18s %s %3d%% %5s/%-5s %3s/%-3s %s' "$n" "$(bar $p)" "$p" "$tick" "$ziel" "$shots" "$soll" "$phase")$'\n'
        done
    done
    wartend=$(ls -1 "$Q/neu"/*.job 2>/dev/null | wc -l)
    fertig=$(ls -1 "$Q/fertig"/*.job 2>/dev/null | wc -l)
    clear
    echo "SotC-Testhelfer   $(date '+%H:%M:%S')"
    echo "Testläufe aktiv: $aktiv   |   Aufträge wartend: $wartend   |   Aufträge erledigt: $fertig"
    echo "Status: $(cut -d'|' -f3- "$T/STATUS.txt" 2>/dev/null)"
    [ -z "$out" ] && out=$'\n'"(gerade kein Auftrag – Claude legt gleich neue an)"
    printf '%s\n' "$out"
    echo "Fotos = Screenshots fertig/geplant. Neue Werte kommen etwa jede Minute."
    sleep 5
done

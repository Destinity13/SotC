#!/usr/bin/env bash
# Vergleich aller Varianten einer Serie -> build/tests/AUSWERTUNG_<serie>.txt
cd "$(dirname "$0")/.."
S="$1"; out="build/tests/AUSWERTUNG_$S.txt"
{
echo "Serie $S  ($(date '+%Y-%m-%d %H:%M'))"; grep '^#' "queue/serien/$S.txt" 2>/dev/null
printf '%-22s %-6s %-9s %-10s %-10s %-9s %-8s %s\n' Variante fertig letztes_Bild Partikel-FS Absturz Fehlschl. Speicher Zaehler@2500/@4000
for d in build/tests/M_${S}__*/; do
    v=$(basename "$d"); v=${v#M_${S}__}
    for l in "$d"*/log.txt; do
        [ -f "$l" ] || continue
        last=$(grep -a -o 'vsync=[0-9]*' "$l" | tail -1 | cut -d= -f2)
        geo=$(grep -a -m1 'SotC geo\] FEHLSCHLAG' "$l" | grep -o 'vsync=[0-9]*' | cut -d= -f2)
        ab=$(grep -a -m1 -B0 'SotC ABORT' "$l" | grep -o 'vsync=[0-9]*' | cut -d= -f2)
        [ -z "$ab" ] && grep -aq 'SotC ABORT' "$l" && ab=ja
        nf=$(grep -a -c 'FEHLSCHLAG Kern-Allokator' "$l")
        mem=$(grep -a 'SotC ptn\] Stand Partition 0x001DC348' "$l" | tail -1 | grep -o 'belegt [0-9.]* MB' | cut -d' ' -f2)
        z=$(grep -a 'SotC zaehler\] vsync=\(2500\|4000\)' "$l" | sed 's/.*vsync=/@/' | tr '\n' ' ' | cut -c1-200)
        printf '%-22s %-6s %-9s %-10s %-10s %-9s %-8s %s\n' "$v" "$([ -f "$d/FERTIG" ] && echo ja || echo nein)" "${last:-?}" "${geo:--}" "${ab:--}" "$nf" "${mem:--}" "$z"
    done
done
} > "$out"
cat "$out"

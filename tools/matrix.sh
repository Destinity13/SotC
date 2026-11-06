#!/usr/bin/env bash
# Experiment-Serie anlegen: viele Varianten desselben Tests laufen parallel, danach automatischer Vergleich.
#   tools/matrix.sh <serie> <beschreibungsdatei>
# Beschreibungsdatei:
#   # Kommentar (Ziel der Serie)
#   BASIS KEY=VAL KEY=VAL ...        gilt für alle Varianten
#   SZENARIO name|eingaben|shots|exit|skip
#   VARIANTE <name> KEY=VAL ...      eine Zeile pro Variante (ohne KEY=VAL = Referenz)
#                                    optional SZENARIO=... ersetzt das Szenario (z. B. Start aus Spielstand)
set -euo pipefail
cd "$(dirname "$0")/.."
S="$1"; D="$2"; mkdir -p queue/serien
cp "$D" "queue/serien/$S.txt"
basis=$(grep '^BASIS ' "$D" | sed 's/^BASIS //' | tr ' ' '\n' | grep '=' || true)
szen=$(grep '^SZENARIO ' "$D" | sed 's/^SZENARIO //')
n=0
while read -r _ name rest; do
    f="queue/neu/M_${S}__${name}.job"
    # Eine Variante darf ihr eigenes Szenario mitbringen: SZENARIO=name|eingaben|shots|exit|skip
    vsz=$(echo "$rest" | tr ' ' '\n' | grep '^SZENARIO=' | sed 's/^SZENARIO=//' || true)
    { echo "# Serie $S, Variante $name"; echo "$basis"; echo "$rest" | tr ' ' '\n' | grep '=' | grep -v '^SZENARIO=' || true; echo "${vsz:-$szen}"; } | grep -v '^$' > "$f.tmp"
    mv "$f.tmp" "$f"; n=$((n+1))
done < <(grep '^VARIANTE ' "$D")
echo "Serie $S: $n Varianten eingereiht"

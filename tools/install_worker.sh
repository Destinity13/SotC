#!/usr/bin/env bash
# Richtet den SotC-Testhelfer ein: startet ihn jetzt und ab sofort bei jeder Anmeldung (KDE-Autostart).
#   ./tools/install_worker.sh        einrichten und starten
#   ./tools/install_worker.sh --aus  wieder entfernen und stoppen
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
DESK="$HOME/.config/autostart/sotc-worker.desktop"

if [ "${1:-}" = "--aus" ]; then
    rm -f "$DESK"
    pkill -f "tools/sotc_worker.sh" || true
    echo "Testhelfer entfernt und gestoppt."
    exit 0
fi

chmod +x "$ROOT/tools/sotc_worker.sh" "$ROOT/tools/sotc_bench.sh"
mkdir -p "$(dirname "$DESK")"
cat > "$DESK" <<EOF
[Desktop Entry]
Type=Application
Name=SotC Testhelfer
Comment=Arbeitet SotC-Testaufträge von Claude ab
Exec="$ROOT/tools/sotc_worker.sh"
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
setsid nohup "$ROOT/tools/sotc_worker.sh" > /tmp/sotc_worker.log 2>&1 < /dev/null &
sleep 2
echo "Testhelfer läuft und startet ab jetzt bei jeder Anmeldung automatisch."
cat "$ROOT/build/tests/STATUS.txt" 2>/dev/null || true

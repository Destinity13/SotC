#!/usr/bin/env bash
# Startet die native SotC-Binary.
#   build/run/ enthält die gelinkte ELF als SCES_533.26 plus Links auf die Disc-Dateien
#   (die Runtime nimmt den Ordner der ELF als cdrom0:), Sektorzugriffe gehen direkt ins ISO.
# Optionen (Umgebungsvariablen):
#   SOTC_WATCHDOG=10     alle 10 s Thread-/Interrupt-Status ausgeben
#   SOTC_GSREGS=1        GS-Display-Register im Watchdog ausgeben
#   SOTC_TRACE_SIF=1     IOP-Uploads protokollieren
#   PS2X_LAZY_VIF1_CHAIN=1  experimentell: kommt nach "New Game" weiter (Grafik noch kaputt)
# Steuerung im Spielfenster (ein Fenster fuer Bild + Eingabe):
#   Gamepad (Xbox/PlayStation/Switch, SDL3) wird automatisch erkannt.
#   Tastatur: WASD = linker Stick, IJKL = Kamera (rechter Stick), Pfeile = Steuerkreuz, X/Leertaste = Kreuz, C = Kreis,
#             Z = Quadrat, V = Dreieck, Q/E = L1/R1, Shift links/R = L2/R2, F/G = L3/R3, Enter = Start, Tab = Select
#   F11 oder Alt+Enter = Vollbild, Fenster schliessen = Spiel beenden.  PS2XGS_FULLSCREEN=1: direkt im Vollbild starten
# Speicherkarte: build/Mcd001.ps2 (PCSX2-Format, wird beim ersten Start leer angelegt)
# Aufruf aus dem Projektordner:  ./tools/run_game.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
RUN="$ROOT/build/run"
mkdir -p "$RUN"
for f in "$ROOT"/extracted/*; do
    name="$(basename "$f")"
    [ "$name" = "SCES_533.26" ] && continue
    ln -sfn "$f" "$RUN/$name"
done
cp -f "$ROOT/elf/sotc_linked.elf" "$RUN/SCES_533.26"
BIN="${SOTC_BIN:-$ROOT/build/sotc/ps2EntryRunner_pc138}"   # aktuelle Binary (pc138: VU1 -13 % [Lower-Befehle direkt, Buchhaltung, XGKICK am Stueck], frisches PGO; pc126: strcmp newlib-exakt; pc125: MOVZ/MOVN 64 Bit; pc124: VDIV/VMAX/VMINI/SQRT.S/PMULTW/rand/newlib-sinf-cosf/PAL-Hblank wie PS2; pc118: HLE-Pools/Rueckruf-Stapel aus Spielspeicher in Kernel-RAM, Spiel beenden geht; pc113: Interrupt-Stapel nicht mehr auf Hauptstapel; pc108: SQRT.S liest ft wie PCSX2 = Laufen/Stick-Staerke repariert; pc107: Tickmap; pc105: PS2-genaue FPU-Vergleiche, chop_daz, Kamera-Unschaerfe-Absturz behoben, EE-Rundung daz, DIV wie PCSX2, Eingabe-Aufnahme, VU0 ohne Abschneiden, Systemsprache, Bildrate folgt Spiel, Musik-Stream-Fix, VU1-Rekompilierer Stufe 5, MTVU, PGO, PCSX2-GS, Eingabe im Spielfenster)
if [ ! -x "$BIN" ] && [ -f "$BIN.xz" ]; then
    echo "==> Entpacke vorgebaute Binary"
    xz -dkf "$BIN.xz" && chmod +x "$BIN"
fi
export PS2X_MTGS="${PS2X_MTGS:-1}"   # GS-Rasterizer im eigenen Thread (+56 %); "0" = synchron
export PS2X_VU1_FAST="${PS2X_VU1_FAST:-1}"   # VU1 ohne long-double-Nachrechnung (bildgleich, ~7 % schneller)
export PS2X_MTVU="${PS2X_MTVU:-1}"         # VIF1+VU1 auf eigenem Thread (wie PCSX2 MTVU)
export PS2X_VU1_REC="${PS2X_VU1_REC:-1}"   # vorab uebersetzte VU1-Programme
# Grafik ueber PCSX2s Vulkan-Renderer (eigenes Fenster "Shadow of the Colossus (SotC Recomp)"); PS2X_PCSX2_GS= leer = alter Software-Renderer
GSB="$ROOT/../gsb/bau_pcsx2/bin"
export PS2X_PCSX2_GS="${PS2X_PCSX2_GS-$GSB/libps2xgs.so}"
export PS2XGS_APPROOT="${PS2XGS_APPROOT:-$GSB}"
export PS2X_PCSX2_UPSCALE="${PS2X_PCSX2_UPSCALE:-3}"   # 3 = 1920x1440-intern auf der 7900 XT
# Bildrate aus dem Spielmenue (~/.config/sotc-recomp/einstellungen.ini: bildrate=60|50). SotC rechnet mit echter
# Zeit pro Bild, laeuft bei 60 Hz also gleich schnell, nur fluessiger.
INI="${XDG_CONFIG_HOME:-$HOME/.config}/sotc-recomp/einstellungen.ini"
HZ_INI=60
[ -f "$INI" ] && HZ_INI="$(sed -n 's/^bildrate=\([0-9]*\).*/\1/p' "$INI" | tail -1)"
[ "$HZ_INI" = 50 ] || HZ_INI=60
# Erzwungene 60 Hz brechen die Figur-Physik. Richtig: das Spiel waehlt selbst (Optionen -> 50/60 Hz), der Port folgt
# dem Videomodus (SetGsCrt NTSC/PAL) automatisch. Start immer mit 50 Hz (PAL).
HZ_INI=50
export PS2X_VBLANK_AUTO="${PS2X_VBLANK_AUTO:-1}"
# EE-FPU wie PS2/PCSX2 fuer SotC: Richtung null runden + keine Denormalen (GameDB eeRoundMode 3). Die frueheren
# Probleme mit "chop" (weisses Bild, Schweben) kamen von Wirts-Vergleichen mit PS2-MAX-Werten, seit pc105 behoben.
export PS2X_EE_ROUND="${PS2X_EE_ROUND:-chop_daz}"
export PS2X_VBLANK_HZ="${PS2X_VBLANK_HZ:-$HZ_INI}"   # PAL SCES-53326: 50 Hz wie PS2/PCSX2 (Partikel-Zeitschritt bitgleich 0.0196608); "60" fuer NTSC-Takt
export SOTC_MEMCARD="${SOTC_MEMCARD:-$ROOT/build/Mcd001.ps2}"
# Speicherkarte: bei jedem Start eine Tageskopie (build/speicherkarten_sicherung/Mcd001_JJJJ-MM-TT.ps2)
if [ -f "$SOTC_MEMCARD" ]; then
    mkdir -p "$ROOT/build/speicherkarten_sicherung"
    cp -f "$SOTC_MEMCARD" "$ROOT/build/speicherkarten_sicherung/Mcd001_$(date +%F).ps2" || true
fi
# Eingabe-Aufnahme je VSync + Speicherkarte zum Startzeitpunkt: so laesst sich jeder Fehler im Testlauf exakt nachspielen
export SOTC_INPUT_RECORD="${SOTC_INPUT_RECORD:-$ROOT/build/eingabe_aufnahme.txt}"
[ -f "$SOTC_MEMCARD" ] && cp -f "$SOTC_MEMCARD" "$ROOT/build/aufnahme_karte.ps2" || true
export SOTC_ISO="${SOTC_ISO:-$ROOT/../Shadow of the Colossus (Europe, Australia) (En,Fr,De,Es,It).iso}"
exec "$BIN" "$RUN/SCES_533.26"

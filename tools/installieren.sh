#!/usr/bin/env bash
# SotC Recomp - Installer fuer Linux (KDE/GNOME/sonstige XDG-Desktops)
#
#   ./tools/installieren.sh             installieren / aktualisieren
#   ./tools/installieren.sh --entfernen Startmenue-Eintrag, Icon und Starter wieder entfernen
#
# Was passiert:
#   1. prueft, ob Spiel-Binary und Grafikbibliothek gebaut sind
#   2. fragt (einmalig) nach DEINEM Spiel-Abbild (ISO von der eigenen Disc, SCES-53326) und merkt es sich
#   3. importiert Texturpakete aus texturpakete/ (*.7z, *.zip, *.rar) nach build/texturquellen/<Name>/
#   4. legt Starter (~/.local/bin/sotc-recomp), Icon und Startmenue-Eintrag an, auf Wunsch auch auf dem Schreibtisch
# Es wird nichts heruntergeladen und nichts ausserhalb von ~/.local, ~/.config/sotc-recomp und dem Projekt angefasst.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
APP=sotc-recomp
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/sotc-recomp"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

gruen() { printf '\033[1;32m%s\033[0m\n' "$*"; }
gelb()  { printf '\033[1;33m%s\033[0m\n' "$*"; }
rot()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }

menue_aktualisieren() {
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" >/dev/null 2>&1
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICON_DIR" >/dev/null 2>&1
    command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1
    command -v kbuildsycoca5 >/dev/null && kbuildsycoca5 >/dev/null 2>&1
    true
}

if [ "${1:-}" = "--entfernen" ]; then
    gelb "SotC Recomp wird aus dem Startmenue entfernt (Spiel, Spielstaende und Einstellungen bleiben erhalten)."
    rm -f "$BIN_DIR/$APP" "$APPS_DIR/$APP.desktop" "$DESKTOP_DIR/$APP.desktop"
    for n in 48 64 128 256 512; do rm -f "$ICON_DIR/${n}x${n}/apps/$APP.png"; done
    menue_aktualisieren
    gruen "Entfernt."
    exit 0
fi

echo
gruen "=== SotC Recomp - Installation ==="
echo

# --- 1. Bausteine pruefen -------------------------------------------------------------------------------------
GAME_BIN="$(sed -n 's/^BIN="\${SOTC_BIN:-\$ROOT\/\(build\/sotc\/ps2EntryRunner_[a-z0-9]*\)}".*/\1/p' tools/run_game.sh | head -1)"
GSB_LIB="$ROOT/../gsb/bau_pcsx2/bin/libps2xgs.so"
fehlt=0
if [ -n "$GAME_BIN" ] && [ -x "$ROOT/$GAME_BIN" ]; then info "Spiel:           $GAME_BIN"; else rot "  Spiel-Binary fehlt ($GAME_BIN) - erst bauen (siehe docs/BUILDING.md)."; fehlt=1; fi
if [ -f "$GSB_LIB" ]; then info "Grafik/Ton:      libps2xgs.so"; else rot "  Grafikbibliothek fehlt ($GSB_LIB) - erst bauen (siehe gsbridge/README.md)."; fehlt=1; fi
[ "$fehlt" = 0 ] || exit 1

# --- 2. Spiel-Abbild (eigene Disc) ------------------------------------------------------------------------------
mkdir -p "$CFG_DIR"
ISO=""
[ -f "$CFG_DIR/iso_pfad" ] && ISO="$(cat "$CFG_DIR/iso_pfad")"
[ -f "$ISO" ] || ISO="$(sed -n 's/^export SOTC_ISO="\${SOTC_ISO:-\(.*\)}"$/\1/p' tools/run_game.sh | sed "s#\$ROOT#$ROOT#" | head -1)"
if [ ! -f "$ISO" ]; then
    gelb "Bitte das Abbild DEINER Disc waehlen (Shadow of the Colossus, Europa, SCES-53326, .iso):"
    if command -v kdialog >/dev/null; then ISO="$(kdialog --title 'SotC Recomp: Spiel-Abbild waehlen' --getopenfilename "$HOME" '*.iso *.ISO' 2>/dev/null)"
    elif command -v zenity >/dev/null; then ISO="$(zenity --file-selection --title='SotC Recomp: Spiel-Abbild waehlen' --file-filter='*.iso *.ISO' 2>/dev/null)"
    else read -r -p "  Pfad zur .iso: " ISO; fi
fi
if [ ! -f "$ISO" ]; then rot "  Kein Spiel-Abbild gewaehlt - Abbruch."; exit 1; fi
if ! head -c 67108864 "$ISO" 2>/dev/null | LC_ALL=C grep -a -q -m1 'SCES_533\.26'; then
    gelb "  Hinweis: im Abbild wurde SCES_533.26 nicht gefunden - ist es die europaeische Version?"
fi
printf '%s\n' "$ISO" > "$CFG_DIR/iso_pfad"
info "Spiel-Abbild:    $ISO"

# --- 3. Texturpakete importieren ---------------------------------------------------------------------------------
QUELLEN="$ROOT/build/texturquellen"
mkdir -p "$QUELLEN" "$ROOT/texturpakete"
entpacken() {  # <archiv> <ziel>
    local a="$1" z="$2"
    case "${a,,}" in
        *.zip) command -v unzip >/dev/null && unzip -q -o "$a" -d "$z" && return 0 ;;
    esac
    command -v 7z  >/dev/null && 7z  x -y -bso0 -bsp0 -o"$z" "$a" && return 0
    command -v 7zz >/dev/null && 7zz x -y -bso0 -bsp0 -o"$z" "$a" && return 0
    command -v bsdtar >/dev/null && bsdtar -xf "$a" -C "$z" && return 0
    python3 -c "import py7zr" 2>/dev/null && python3 -c "import py7zr,sys; py7zr.SevenZipFile(sys.argv[1]).extractall(sys.argv[2])" "$a" "$z" && return 0
    return 1
}
shopt -s nullglob nocaseglob
for a in "$ROOT"/texturpakete/*.7z "$ROOT"/texturpakete/*.zip "$ROOT"/texturpakete/*.rar; do
    base="$(basename "$a")"; name="$(printf '%s' "${base%.*}" | tr -cs 'A-Za-z0-9' '-' | sed 's/^-//; s/-$//' | tr 'A-Z' 'a-z' | cut -c1-60)"
    if [ -f "$QUELLEN/.importiert_$name" ]; then info "Texturpaket:     $base (schon importiert)"; continue; fi
    # Origami-Paket wurde schon von Hand eingerichtet?
    case "$name" in *origami*) [ -d "$QUELLEN/origami-remaster" ] && { touch "$QUELLEN/.importiert_$name"; info "Texturpaket:     $base (als origami-remaster vorhanden)"; continue; } ;; esac
    tmp="$QUELLEN/.entpacken_$name"; mkdir -p "$tmp"
    info "Texturpaket:     $base wird entpackt ..."
    if ! entpacken "$a" "$tmp"; then rot "  Konnte $base nicht entpacken (7z/unzip/bsdtar fehlt?) - uebersprungen."; continue; fi
    # Ersatzordner finden (SCES-53326/replacements oder replacements), sonst alles
    rep="$(find "$tmp" -type d -ipath '*SCES-53326/replacements' | head -1)"
    [ -n "$rep" ] || rep="$(find "$tmp" -type d -iname 'replacements' | head -1)"
    [ -n "$rep" ] || rep="$tmp"
    mv "$rep" "$QUELLEN/$name" && touch "$QUELLEN/.importiert_$name"
    n="$(find "$QUELLEN/$name" -type f \( -iname '*.png' -o -iname '*.dds' \) | wc -l)"
    info "                 -> $n Texturen in build/texturquellen/$name"
    rm -rf "$tmp"
done
shopt -u nullglob nocaseglob

# --- 4. Starter, Icon, Startmenue ----------------------------------------------------------------------------------
mkdir -p "$BIN_DIR" "$APPS_DIR"
cat > "$BIN_DIR/$APP" <<EOF
#!/usr/bin/env bash
# SotC Recomp Starter (erzeugt von tools/installieren.sh)
export SOTC_ISO="\${SOTC_ISO:-\$(cat "$CFG_DIR/iso_pfad" 2>/dev/null)}"
cd "$ROOT" || exit 1
exec ./tools/run_game.sh "\$@" > build/letzter_start.log 2>&1
EOF
chmod +x "$BIN_DIR/$APP"
for n in 48 64 128 256 512; do
    mkdir -p "$ICON_DIR/${n}x${n}/apps"
    src="$ROOT/tools/installer/sotc-recomp_${n}.png"; [ "$n" = 512 ] && src="$ROOT/tools/installer/sotc-recomp.png"
    [ -f "$src" ] && cp -f "$src" "$ICON_DIR/${n}x${n}/apps/$APP.png"
done
cat > "$APPS_DIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Shadow of the Colossus
GenericName=SotC Recomp
Comment=Nativer Linux-Port (Static Recompilation) - HD, 60 FPS
Exec=$BIN_DIR/$APP
Icon=$APP
Terminal=false
Categories=Game;ActionGame;AdventureGame;
Keywords=sotc;colossus;wander;agro;ps2;recomp;
StartupWMClass=SotC Recomp
StartupNotify=true
EOF
chmod +x "$APPS_DIR/$APP.desktop"
info "Startmenue:      Spiele -> Shadow of the Colossus"
if [ -d "$DESKTOP_DIR" ] && [ "${SOTC_SCHREIBTISCH:-1}" = 1 ]; then
    cp -f "$APPS_DIR/$APP.desktop" "$DESKTOP_DIR/$APP.desktop"; chmod +x "$DESKTOP_DIR/$APP.desktop"
    command -v gio >/dev/null && gio set "$DESKTOP_DIR/$APP.desktop" metadata::trusted true 2>/dev/null
    info "Schreibtisch:    Verknuepfung angelegt"
fi
menue_aktualisieren

# --- Optional: ReShade-artige Effekte -------------------------------------------------------------------------------
if ! { command -v pacman >/dev/null && pacman -Q vkbasalt >/dev/null 2>&1; } && [ ! -f /usr/lib/libvkbasalt.so ] && [ ! -f /usr/lib64/libvkbasalt.so ]; then
    info "Optional:        fuer Effekte im ReShade-Stil 'vkbasalt' installieren (CachyOS: sudo pacman -S vkbasalt)"
fi

echo
gruen "Fertig! Starten ueber das Startmenue (Spiele -> Shadow of the Colossus) oder mit: sotc-recomp"
info "Im Spiel: ESC / F1 / Guide-Taste oeffnet das Menue (Grafik, Shader, Texturen, Ton, Steuerung)."
info "Entfernen: ./tools/installieren.sh --entfernen"
echo

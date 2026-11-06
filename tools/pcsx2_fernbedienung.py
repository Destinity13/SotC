#!/usr/bin/env python3
"""PCSX2-Fernbedienung: Claude steuert einen laufenden PCSX2 Schritt für Schritt über eine Befehlsdatei.

  python3 -I tools/pcsx2_fernbedienung.py            (startet PCSX2 + virtuelles Gamepad)
  Befehle (je Zeile, an referenz/steuerung/befehle.txt anhängen):
    drueck <taste> [ticks]        Taste kurz drücken (Standard 6 Ticks = 0,12 s), Tasten wie in den Testskripten
    halte <taste> <ticks>         Taste/Stick halten
    warte <sekunden>
    bild <name>                   Savestate + Bildschirmfoto -> referenz/steuerung/<name>.p2s/.png
    lade <datei.p2s>              Savestate laden (Datei wird in den Savestate-Ordner kopiert)
    ende
  Status/Protokoll: referenz/steuerung/protokoll.txt
"""
import glob
import os
import shutil
import struct
import socket
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pcsx2_referenz import BTN, STICK, CFG, REF, ROOT, SST, make_pad, pine, game_id  # noqa: E402
from evdev import ecodes as e  # noqa: E402

STEU = os.path.join(REF, 'steuerung')
CMD = os.path.join(STEU, 'befehle.txt')
LOG = os.path.join(STEU, 'protokoll.txt')


def log(msg):
    line = f'{time.strftime("%H:%M:%S")} {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def press(pad, key, ticks):
    if key in BTN:
        pad.write(e.EV_KEY, BTN[key], 1); pad.syn(); time.sleep(ticks / 50.0)
        pad.write(e.EV_KEY, BTN[key], 0); pad.syn()
    elif key in STICK:
        code, val = STICK[key]
        pad.write(e.EV_ABS, code, val); pad.syn(); time.sleep(ticks / 50.0)
        pad.write(e.EV_ABS, code, 0); pad.syn()
    else:
        log(f'unbekannte Taste {key}')


def snapshot(name):
    before = {f: os.path.getmtime(f) for f in glob.glob(os.path.join(SST, '*.p2s'))}
    pine(0x09, bytes([1]))
    for _ in range(60):
        time.sleep(0.1)
        now = {f: os.path.getmtime(f) for f in glob.glob(os.path.join(SST, '*.p2s'))}
        changed = [f for f, m in now.items() if before.get(f) != m]
        if changed:
            time.sleep(0.6)
            dst = os.path.join(STEU, name + '.p2s')
            shutil.copy(changed[0], dst)
            with zipfile.ZipFile(dst) as z:
                png = [n for n in z.namelist() if n.lower().endswith('.png')]
                if png:
                    open(os.path.join(STEU, name + '.png'), 'wb').write(z.read(png[0]))
            log(f'Bild {name} gespeichert')
            return
    log(f'Bild {name}: kein Savestate entstanden')


def main():
    os.makedirs(STEU, exist_ok=True)
    os.makedirs(SST, exist_ok=True)
    open(CMD, 'a').close()
    done = len(open(CMD).read().splitlines())   # alte Befehle nicht erneut ausführen
    app = sorted(glob.glob(os.path.join(REF, 'pcsx2*.AppImage')))[-1]
    iso = os.environ.get('SOTC_ISO') or os.path.join(os.path.dirname(ROOT), 'Shadow of the Colossus (Europe, Australia) (En,Fr,De,Es,It).iso')
    env = dict(os.environ, XDG_CONFIG_HOME=CFG, APPIMAGE_EXTRACT_AND_RUN='1')
    pad = make_pad()
    time.sleep(1.0)
    proc = subprocess.Popen([app, '-batch', '-nogui', '-fastboot', '--', iso], env=env,
                            stdout=open(os.path.join(STEU, 'pcsx2.log'), 'w'), stderr=subprocess.STDOUT)
    for _ in range(1200):
        time.sleep(0.05)
        try:
            if game_id():
                break
        except OSError:
            pass
    log('PCSX2 läuft, warte auf Befehle')
    idle = time.monotonic()
    while proc.poll() is None:
        lines = open(CMD).read().splitlines()
        if len(lines) <= done:
            if time.monotonic() - idle > 1800:
                log('30 Minuten ohne Befehl, beende')
                break
            time.sleep(0.2)
            continue
        cmd = lines[done].split()
        done += 1
        idle = time.monotonic()
        if not cmd:
            continue
        log('Befehl: ' + ' '.join(cmd))
        op = cmd[0]
        if op == 'drueck':
            press(pad, cmd[1], int(cmd[2]) if len(cmd) > 2 else 6)
        elif op == 'halte':
            press(pad, cmd[1], int(cmd[2]))
        elif op == 'warte':
            time.sleep(float(cmd[1]))
        elif op == 'bild':
            snapshot(cmd[1])
        elif op == 'lade':
            src = cmd[1] if os.path.isabs(cmd[1]) else os.path.join(STEU, cmd[1])
            serial = [f for f in glob.glob(os.path.join(SST, '*.p2s'))]
            target = serial[0] if serial else os.path.join(SST, 'SCES-53326 (00000000).01.p2s')
            shutil.copy(src, target)
            r = pine(0x0A, bytes([int(target.rsplit('.', 2)[-2])]))
            log(f'Savestate {cmd[1]} geladen ({r.hex()})')
        elif op == 'ende':
            break
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
    pad.close()
    log('beendet')


if __name__ == '__main__':
    main()

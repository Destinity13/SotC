#!/usr/bin/env python3
"""Automatischer PCSX2-Referenzlauf für den SotC-Recomp-Vergleich.

  python3 -I tools/pcsx2_referenz.py <name> "<eingaben>" <savestate-ticks> <ende-tick>

  eingaben: dasselbe Format wie die Testskripte (z. B. "330:x,480:x,930:start,1900:ldown*600"),
            Tick = VSync (PAL 50 Hz) ab Spielstart (Zeitpunkt, ab dem PINE die Spiel-ID meldet).
  Ablauf:   PCSX2 (AppImage, portabel unter referenz/) starten, virtuelles Gamepad (uinput, meldet sich
            als Xbox-360-Controller, den SDL ohne eigenes Mapping kennt) anlegen, Tasten nach Zeitplan
            drücken und an den angegebenen Ticks per PINE-IPC einen Savestate anlegen.
  Ergebnis: referenz/laeufe/<name>/<tick>.p2s  -> Auswertung mit tools/p2s_auswerten.py
  PINE (PCSX2): Nachricht [u32 Gesamtlänge][u8 Befehl][Argumente], Antwort [u32 Länge][u8 Ergebnis][Daten].
                0x09 SaveState(u8 Slot), 0x0C Spiel-ID, 0x0F Status.
"""
import glob
import os
import shutil
import socket
import struct
import subprocess
import sys
import time

from evdev import AbsInfo, UInput, ecodes as e

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, 'referenz')
CFG = os.path.join(REF, 'cfg')
SST = os.path.join(REF, 'sstates')
VSYNC_HZ = 50.0

BTN = {'x': e.BTN_SOUTH, 'o': e.BTN_EAST, 't': e.BTN_NORTH, 's': e.BTN_WEST, 'l1': e.BTN_TL, 'r1': e.BTN_TR,
       'select': e.BTN_SELECT, 'start': e.BTN_START}
STICK = {'lup': (e.ABS_Y, -32767), 'ldown': (e.ABS_Y, 32767), 'lleft': (e.ABS_X, -32767), 'lright': (e.ABS_X, 32767),
         'rup': (e.ABS_RY, -32767), 'rdown': (e.ABS_RY, 32767), 'rleft': (e.ABS_RX, -32767), 'rright': (e.ABS_RX, 32767),
         'dup': (e.ABS_HAT0Y, -1), 'ddown': (e.ABS_HAT0Y, 1), 'dleft': (e.ABS_HAT0X, -1), 'dright': (e.ABS_HAT0X, 1)}


def make_pad():
    caps = {
        e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START,
                   e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
        e.EV_ABS: [(a, AbsInfo(0, -32768, 32767, 16, 128, 0)) for a in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY)] +
                  [(a, AbsInfo(0, 0, 255, 0, 0, 0)) for a in (e.ABS_Z, e.ABS_RZ)] +
                  [(a, AbsInfo(0, -1, 1, 0, 0, 0)) for a in (e.ABS_HAT0X, e.ABS_HAT0Y)],
    }
    return UInput(caps, name='Microsoft X-Box 360 pad', vendor=0x045E, product=0x028E, version=0x0114, bustype=e.BUS_USB)


def plan(spec):
    ev = []   # (tick, art, code, wert)
    for item in filter(None, spec.split(',')):
        t, key = item.split(':', 1)
        t = int(t)
        n = 6
        if '*' in key:
            key, n = key.split('*')
            n = int(n)
        if key in BTN:
            ev += [(t, e.EV_KEY, BTN[key], 1), (t + n, e.EV_KEY, BTN[key], 0)]
        elif key in STICK:
            code, val = STICK[key]
            ev += [(t, e.EV_ABS, code, val), (t + n, e.EV_ABS, code, 0)]
        else:
            print(f'unbekannte Taste {key}, übersprungen')
    return sorted(ev)


def pine(cmd, payload=b''):
    path = os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'pcsx2.sock')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(20)
        s.connect(path)
        s.sendall(struct.pack('<IB', 5 + len(payload), cmd) + payload)
        hdr = s.recv(4)
        size = struct.unpack('<I', hdr)[0]
        data = b''
        while len(data) < size - 4:
            chunk = s.recv(size - 4 - len(data))
            if not chunk:
                break
            data += chunk
        return data   # [Ergebnis][Daten]


def game_id():
    d = pine(0x0C)
    if len(d) < 5 or d[0] != 0:
        return ''
    n = struct.unpack('<I', d[1:5])[0]
    return d[5:5 + n].split(b'\0')[0].decode(errors='replace')


def main():
    name, spec = sys.argv[1], sys.argv[2]
    shots, end = [int(x) for x in sys.argv[3].split(',')], int(sys.argv[4])
    out = os.path.join(REF, 'laeufe', name)
    os.makedirs(out, exist_ok=True)
    os.makedirs(SST, exist_ok=True)
    app = sorted(glob.glob(os.path.join(REF, 'pcsx2*.AppImage')))[-1]
    iso = os.environ.get('SOTC_ISO') or os.path.join(os.path.dirname(ROOT), 'Shadow of the Colossus (Europe, Australia) (En,Fr,De,Es,It).iso')
    env = dict(os.environ, XDG_CONFIG_HOME=CFG, APPIMAGE_EXTRACT_AND_RUN='1')
    for f in glob.glob(os.path.join(SST, '*.p2s')):
        os.remove(f)
    pad = make_pad()
    time.sleep(1.0)
    log = open(os.path.join(out, 'pcsx2.log'), 'w')
    proc = subprocess.Popen([app, '-batch', '-nogui', '-fastboot', '--', iso], env=env, stdout=log, stderr=subprocess.STDOUT)
    t0 = None
    for _ in range(1200):
        time.sleep(0.05)
        if proc.poll() is not None:
            raise SystemExit(f'PCSX2 beendet sich sofort (rc={proc.returncode}), siehe {out}/pcsx2.log')
        try:
            gid = game_id()
        except OSError:
            continue
        if gid:
            t0 = time.monotonic()
            print(f'PCSX2 läuft, Spiel {gid}, Zeit läuft', flush=True)
            break
    if t0 is None:
        proc.kill()
        raise SystemExit('PINE meldet keine Spiel-ID (EnablePINE? ISO-Pfad?)')
    events = plan(spec)
    todo = sorted(shots)
    slot, i = 1, 0
    while True:
        tick = (time.monotonic() - t0) * VSYNC_HZ
        while i < len(events) and events[i][0] <= tick:
            _, typ, code, val = events[i]
            pad.write(typ, code, val)
            pad.syn()
            i += 1
        if todo and tick >= todo[0]:
            want = todo.pop(0)
            before = set(glob.glob(os.path.join(SST, '*.p2s')))
            r = pine(0x09, bytes([slot]))
            new = []
            for _ in range(40):
                time.sleep(0.1)
                new = sorted(set(glob.glob(os.path.join(SST, '*.p2s'))) - before, key=os.path.getmtime)
                if new:
                    break
            if new:
                time.sleep(0.5)
                shutil.copy(new[-1], os.path.join(out, f'{want:05d}.p2s'))
            print(f'Savestate bei Tick {want} (tatsächlich {tick:.0f}): {"ok" if new else "FEHLER " + r.hex()}', flush=True)
            slot = slot % 9 + 1
        if tick >= end or proc.poll() is not None:
            break
        time.sleep(0.004)
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
    pad.close()
    print('fertig:', sorted(os.listdir(out)), flush=True)


if __name__ == '__main__':
    main()

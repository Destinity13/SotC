#!/usr/bin/env python3
"""PCSX2-Referenzlauf mit einer Port-Aufnahme, Bild fuer Bild ueber den spieleigenen Bildzaehler (0x1DC9EC).

  python3 -I tools/pcsx2_nachspiel.py <name> <aufnahme.txt> <tickmap.txt> <zaehler-liste> <ende-zaehler>

  aufnahme.txt : Zeilen "vsyncTick b0 b1 rx ry lx ly" (SOTC_INPUT_RECORD des Ports)
  tickmap.txt  : Zeilen "vsyncTick spielzaehler" (SOTC_TICKMAP des Ports beim Nachspielen derselben Aufnahme)
  zaehler-liste: Spielzaehler-Werte, an denen ein Savestate (RAM + Bild) entsteht, z. B. 1000,1050,1100
  Ablauf: virtuelles Pad (uinput, Xbox-360-Kennung), PCSX2 unsichtbar unter Xvfb mit Software-GS, DualSense fuer
          PCSX2 ausgeblendet; Eingaben werden gesetzt, sobald PINE (Read32 0x1DC9EC) den jeweiligen Zaehler meldet.
  Ergebnis: referenz/laeufe/<name>/gc<zaehler>.p2s (+ .png)
"""
import math, glob
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import zipfile

from evdev import AbsInfo, UInput, ecodes as e

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, 'referenz')
CFG = os.path.join(REF, 'cfg')
SST = os.path.join(REF, 'sstates')
CNT_ADDR = 0x1DC9EC

# PS2-Tastenbits: b0: SEL L3 R3 START UP RIGHT DOWN LEFT ; b1: L2 R2 L1 R1 TRI O X SQ (0 = gedrueckt)
B0 = {0: e.BTN_SELECT, 1: e.BTN_THUMBL, 2: e.BTN_THUMBR, 3: e.BTN_START}
HAT = {4: (e.ABS_HAT0Y, -1), 5: (e.ABS_HAT0X, 1), 6: (e.ABS_HAT0Y, 1), 7: (e.ABS_HAT0X, -1)}
B1 = {0: ('abs', e.ABS_Z), 1: ('abs', e.ABS_RZ), 2: ('key', e.BTN_TL), 3: ('key', e.BTN_TR),
      4: ('key', e.BTN_NORTH), 5: ('key', e.BTN_EAST), 6: ('key', e.BTN_SOUTH), 7: ('key', e.BTN_WEST)}


def make_pad():
    caps = {
        e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START,
                   e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
        e.EV_ABS: [(a, AbsInfo(0, -32768, 32767, 16, 128, 0)) for a in (e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY)] +
                  [(a, AbsInfo(0, 0, 255, 0, 0, 0)) for a in (e.ABS_Z, e.ABS_RZ)] +
                  [(a, AbsInfo(0, -1, 1, 0, 0, 0)) for a in (e.ABS_HAT0X, e.ABS_HAT0Y)],
    }
    return UInput(caps, name='Microsoft X-Box 360 pad', vendor=0x045E, product=0x028E, version=0x0114, bustype=e.BUS_USB)


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
        return data


def read32(addr):
    d = pine(0x02, struct.pack('<I', addr))
    return struct.unpack('<I', d[1:5])[0] if len(d) >= 5 and d[0] == 0 else None


def player_ptr():
    """Spielerobjekt wie GAMEUTIL_get_player_obj (isysGroupForFirst 0x1477360); 0 = keins"""
    w = read32(0x1477360)
    if w is None:
        return None
    gid = w & 0xFFFF
    for _ in range(4096):
        if not gid:
            return 0
        node = 0x216B00 + gid * 24
        c = read32(node + 0xC)
        if c is None:
            return None
        if c & 0xFF:
            gid = c >> 16
            continue
        obj = read32(node)
        if obj is None:
            return None
        f = read32((obj & 0x1FFFFFC) + 8)
        if f is None:
            return None
        if (f >> 16) & 1:
            gid = c >> 16
            continue
        return obj
    return 0


def game_id():
    d = pine(0x0C)
    if len(d) < 5 or d[0] != 0:
        return ''
    n = struct.unpack('<I', d[1:5])[0]
    return d[5:5 + n].split(b'\0')[0].decode(errors='replace')


def load_recording(rec_path, map_path):
    tickmap = {}
    port_player_gc = None
    for line in open(map_path):
        p = line.split()
        if len(p) >= 2:
            if int(p[0]) in tickmap:
                continue
            tickmap[int(p[0])] = int(p[1])
            if len(p) >= 3 and port_player_gc is None and int(p[2], 16) != 0:
                port_player_gc = int(p[1])
    rows = []
    for line in open(rec_path):
        p = line.split()
        if len(p) != 7:
            continue
        vs = int(p[0])
        if vs not in tickmap:
            # naechsten bekannten Tick nehmen
            cand = [t for t in tickmap if t >= vs]
            if not cand:
                continue
            gc = tickmap[min(cand)]
        else:
            gc = tickmap[vs]
        rows.append((gc, [int(x, 16) for x in p[1:]]))
    rows.sort(key=lambda r: r[0])
    return rows, port_player_gc


def apply(pad, state, raw, prev):
    b0, b1, rx, ry, lx, ly = raw
    ev = []
    for bit, code in B0.items():
        ev.append((e.EV_KEY, code, 0 if (b0 >> bit) & 1 else 1))
    hx = hy = 0
    for bit, (code, val) in HAT.items():
        if not (b0 >> bit) & 1:
            if code == e.ABS_HAT0X:
                hx = val
            else:
                hy = val
    ev.append((e.EV_ABS, e.ABS_HAT0X, hx))
    ev.append((e.EV_ABS, e.ABS_HAT0Y, hy))
    for bit, (kind, code) in B1.items():
        down = not (b1 >> bit) & 1
        if kind == 'key':
            ev.append((e.EV_KEY, code, 1 if down else 0))
        else:
            ev.append((e.EV_ABS, code, 255 if down else 0))
    # Achsen so setzen, dass PCSX2 (AxisScale=1, Deadzone=0, PadDualshock2::Set + MERGE) exakt den Portwert v ergibt:
    # v>=128: raw_pos = 2v-255 -> lx = 127 + (raw+1)/2 = v ; v<128: raw_neg = 255-2v -> lx = 127 - raw/2 = v
    def axis(v):
        if v >= 128:
            return min(32767, int(math.ceil((2 * v - 255 + 0.5) / 255.0 * 32767.0)))
        return max(-32768, int(math.floor(-(255 - 2 * v + 0.5) / 255.0 * 32768.0)))
    for code, v in ((e.ABS_X, lx), (e.ABS_Y, ly), (e.ABS_RX, rx), (e.ABS_RY, ry)):
        ev.append((e.EV_ABS, code, axis(v)))
    changed = False
    for typ, code, val in ev:
        if state.get((typ, code)) != val:
            pad.write(typ, code, val)
            state[(typ, code)] = val
            changed = True
    if changed:
        pad.syn()


def extract_png(p2s):
    with zipfile.ZipFile(p2s) as z:
        n = [x for x in z.namelist() if x.lower().endswith('.png')]
        if n:
            open(p2s[:-4] + '.png', 'wb').write(z.read(n[0]))


def main():
    # Nur ein PCSX2-Referenzlauf gleichzeitig (gemeinsamer PINE-Socket und virtuelles Pad)
    import fcntl
    lockf = open('/tmp/sotc_ref.lock', 'w')
    fcntl.flock(lockf, fcntl.LOCK_EX)
    name, rec_path, map_path = sys.argv[1], sys.argv[2], sys.argv[3]
    shots = sorted(int(x) for x in sys.argv[4].split(',') if x)
    end = int(sys.argv[5])
    out = os.path.join(REF, 'laeufe', name)
    os.makedirs(out, exist_ok=True)
    os.makedirs(SST, exist_ok=True)
    for f in glob.glob(os.path.join(SST, '*.p2s')):
        os.remove(f)
    rows, port_pgc = load_recording(rec_path, map_path)
    print(f'{len(rows)} Eingabe-Eintraege, Spielzaehler {rows[0][0]}..{rows[-1][0]}, Spieler im Port ab Zaehler {port_pgc}', flush=True)
    if port_pgc is not None:
        rows = [r for r in rows if r[0] >= port_pgc]
    app = sorted(glob.glob(os.path.join(REF, 'pcsx2*.AppImage')))[-1]
    iso = os.environ.get('SOTC_ISO') or os.path.join(os.path.dirname(ROOT), 'Shadow of the Colossus (Europe, Australia) (En,Fr,De,Es,It).iso')
    env = dict(os.environ, XDG_CONFIG_HOME=CFG, APPIMAGE_EXTRACT_AND_RUN='1',
               SDL_GAMECONTROLLER_IGNORE_DEVICES='0x054C/0x0CE6,0x054C/0x0DF2,0x054C/0x05C4,0x054C/0x09CC',
               SDL_VIDEODRIVER='x11', QT_QPA_PLATFORM='xcb')
    pad = make_pad()
    time.sleep(1.0)
    log = open(os.path.join(out, 'pcsx2.log'), 'w')
    base = [app, '-batch', '-nogui', '-fastboot', '--', iso]
    if os.environ.get('SOTC_REF_SICHTBAR'):
        cmd = base
    elif shutil.which('xvfb-run'):
        cmd = ['xvfb-run', '-a', '-s', '-screen 0 1280x960x24'] + base
    elif shutil.which('kwin_wayland'):
        # unsichtbarer KWin (virtuelles Backend) mit Xwayland, PCSX2 laeuft darin als X11-Programm
        env['WAYLAND_DISPLAY'] = 'sotc-ref'
        env.pop('DISPLAY', None)
        wrap = '/tmp/sotc_ref_start_pcsx2.sh'  # ohne Leerzeichen: KWin trennt den Programmpfad an Leerzeichen
        with open(wrap, 'w') as w:
            w.write('#!/bin/sh\nexec ' + ' '.join("'" + x.replace("'", "'\\''") + "'" for x in base) + ' >> ' + "'" + os.path.join(out, 'pcsx2_app.log') + "'" + ' 2>&1\n')
        os.chmod(wrap, 0o755)
        cmd = ['kwin_wayland', '--virtual', '--xwayland', '--width', '1280', '--height', '960', '--socket', 'sotc-ref', wrap]
        env['QT_QPA_PLATFORM'] = 'wayland'
        env['SDL_VIDEODRIVER'] = 'wayland'
    else:
        cmd = base
    print('Start:', ' '.join(cmd[:8]), flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    gid = ''
    for _ in range(3600):
        time.sleep(0.05)
        if proc.poll() is not None:
            raise SystemExit(f'PCSX2 beendet sich sofort (rc={proc.returncode}), siehe {out}/pcsx2.log')
        try:
            gid = game_id()
        except OSError:
            continue
        if gid:
            break
    if not gid:
        os.killpg(proc.pid, 9)
        raise SystemExit('PINE meldet keine Spiel-ID')
    print(f'PCSX2 laeuft, Spiel {gid}', flush=True)
    state = {}
    i = 0
    todo = list(shots)
    # Phase 1: Menues/Intro mit X/START durchklicken, bis das Spielerobjekt existiert (Lade-/Bootzeiten von Port
    # und PCSX2 unterscheiden sich, ein Zaehler-Abgleich ab Spielstart waere falsch). Danach Aufnahme mit Versatz.
    offset = 0
    if port_pgc is not None:
        neutral = [0xFF, 0xFF, 0x80, 0x80, 0x80, 0x80]
        x_down = [0xFF, 0xBF, 0x80, 0x80, 0x80, 0x80]
        st_down = [0xF7, 0xFF, 0x80, 0x80, 0x80, 0x80]
        t_menu = time.monotonic()
        seen_start = False
        sync = [float(x) for x in os.environ['SOTC_REF_SYNC'].split(',')] if os.environ.get('SOTC_REF_SYNC') else None
        if sync: sync[3] = int(sync[3])
        k = 0
        while True:
            try:
                pp = player_ptr()
                gc0 = read32(CNT_ADDR)
            except OSError:
                time.sleep(0.05)
                continue
            if pp and not sync:
                offset = gc0 - port_pgc
                print(f'Spieler da bei PCSX2-Zaehler {gc0} (Port {port_pgc}), Versatz {offset}', flush=True)
                break
            if pp and sync:
                try:
                    pos = [struct.unpack('<f', struct.pack('<I', read32(pp + 0xa0 + 4 * j)))[0] for j in range(3)]
                except OSError:
                    pos = None
                # SOTC_REF_SYNC2=<port-gc>: statt des Erscheinens der Position den ersten Physikschritt nehmen
                # (y weicht erstmals von der Startposition ab). PCSX2 laedt langsamer: zwischen Erscheinen und
                # Physikstart liegen dort ~100 Bilder mehr als im Port.
                sync2 = os.environ.get('SOTC_REF_SYNC2')
                if pos and sync2 and all(abs(pos[j] - sync[j]) < 3.0 for j in range(3)):
                    seen_start = True
                if pos and sync2 and seen_start and abs(pos[1] - sync[1]) > 0.05:
                    offset = gc0 - int(sync2) + int(os.environ.get('SOTC_REF_SHIFT', '0'))
                    print(f'Physikstart (y {pos[1]:.3f}) bei PCSX2-Zaehler {gc0} (Port {sync2}), Versatz {offset}', flush=True)
                    port_pgc = int(sync2)
                    break
                if pos and not sync2 and all(abs(pos[j] - sync[j]) < 3.0 for j in range(3)):
                    offset = gc0 - sync[3] + int(os.environ.get('SOTC_REF_SHIFT', '0'))
                    print(f'Spielbeginn (Position {pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) bei PCSX2-Zaehler {gc0} (Port {sync[3]}), Versatz {offset} (Verschiebung {os.environ.get("SOTC_REF_SHIFT", "0")})', flush=True)
                    port_pgc = sync[3]
                    break
            if time.monotonic() - t_menu > 1200:
                print('Spielerobjekt nie erschienen', flush=True)
                break
            phase = (gc0 or 0) // 30
            if phase != k:
                k = phase
                apply(pad, state, (x_down if k % 4 == 0 else st_down if k % 4 == 2 else neutral), None)
            time.sleep(0.01)
        apply(pad, state, [0xFF, 0xFF, 0x80, 0x80, 0x80, 0x80], None)
        rows = [(g + offset, raw) for g, raw in rows if g >= port_pgc]
        todo = [t + offset for t in todo]
        end = end + offset
        shot_names = {t + offset: t for t in shots}
    else:
        shot_names = {t: t for t in shots}
    slot = 1
    last_gc = -1
    stale = 0
    t_start = time.monotonic()
    while True:
        try:
            gc = read32(CNT_ADDR)
        except OSError:
            gc = None
        if gc is None:
            time.sleep(0.01)
            continue
        if gc == last_gc:
            stale += 1
        else:
            stale = 0
            last_gc = gc
        while i < len(rows) and rows[i][0] <= gc:
            apply(pad, state, rows[i][1], None)
            i += 1
        if todo and gc >= todo[0]:
            want = todo.pop(0)
            before = set(glob.glob(os.path.join(SST, '*.p2s')))
            r = pine(0x09, bytes([slot]))
            new = []
            for _ in range(100):
                time.sleep(0.1)
                new = sorted(set(glob.glob(os.path.join(SST, '*.p2s'))) - before, key=os.path.getmtime)
                if new:
                    break
            if new:
                # warten bis Datei fertig geschrieben
                sz = -1
                for _ in range(50):
                    s2 = os.path.getsize(new[-1])
                    if s2 == sz and sz > 0:
                        break
                    sz = s2
                    time.sleep(0.2)
                dst = os.path.join(out, f'gc{shot_names.get(want, want):06d}.p2s')
                shutil.copy(new[-1], dst)
                try:
                    extract_png(dst)
                except Exception as ex:
                    print('  (kein Bild im Savestate:', ex, ')', flush=True)
            print(f'Savestate bei Port-Zaehler {shot_names.get(want, want)} (PCSX2 {gc}): {"ok" if new else "FEHLER " + r.hex()}', flush=True)
            slot = slot % 9 + 1
        if gc >= end or proc.poll() is not None or (time.monotonic() - t_start) > 1500:
            break
        time.sleep(0.003)
    try:
        os.killpg(proc.pid, 15)
        proc.wait(15)
    except Exception:
        try:
            os.killpg(proc.pid, 9)
        except Exception:
            pass
    pad.close()
    print('fertig:', sorted(os.listdir(out)), flush=True)


if __name__ == '__main__':
    main()

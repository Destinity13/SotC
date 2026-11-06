#!/usr/bin/env python3
"""Wertet PCSX2-Savestates (.p2s) bzw. RAM-Abbilder für den Vergleich mit dem SotC-Recomp aus.

  python3 -I tools/p2s_auswerten.py referenz/*.p2s [build/tests/.../sotc_ram.bin]

Ausgabe je Datei:
  - Partitions-Baum wie iosDebugPartitionDump (0x1AB358 -> 0x1AA720): belegt/frei/größter Block, Halter-PC
  - Partikel-Pool 0x01625F44 (falls vorhanden) und der Fußstaub-Zustand (Layout-Klasse +0x90)
Blockformat (aus dem Spielcode rekonstruiert): Partition +0x08 erster Block (Wörter);
Block-Kopf 0x20 Byte: +0x00 Größe (Wörter), +0x08 nächster Block, +0x0C Bit24 belegt / Bit25 Unterpartition
(Kopf bei Block+0x24), +0x18 Aufrufer-PC (Wörter).
"""
import struct
import sys
import zipfile
from collections import defaultdict

RAM = 32 * 1024 * 1024
ROOT = 0x1DC348


def load(path):
    if path.endswith('.p2s'):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().startswith('eememory')), None)
            if not name:
                raise SystemExit(f'{path}: kein eeMemory im Savestate ({z.namelist()[:8]})')
            data = z.read(name)
    else:
        data = open(path, 'rb').read()
    if len(data) < RAM:
        data = data + b'\0' * (RAM - len(data))
    return data


def r32(m, a):
    a &= RAM - 1
    return struct.unpack_from('<I', m, a)[0]


def rf(m, a):
    a &= RAM - 1
    return struct.unpack_from('<f', m, a)[0]


def walk(m, part, depth=0, out=None):
    if depth > 6 or part >= RAM:
        return
    per = defaultdict(lambda: [0, 0])
    used = free = largest = nu = nf = 0
    subs = []
    b = (r32(m, part + 8) & 0xFFFFFF) << 2
    guard = 0
    while b and b < RAM and guard < 400000:
        guard += 1
        w0, wc = r32(m, b), r32(m, b + 0xC)
        size = (w0 & 0xFFFFFF) << 2
        if (wc >> 24) & 1:
            used += size; nu += 1
            pc = (r32(m, b + 0x18) & 0xFFFFFF) << 2
            per[pc][0] += size; per[pc][1] += 1
            if (wc >> 25) & 1:
                subs.append(b + 0x24)
        else:
            free += size; nf += 1; largest = max(largest, size)
        nxt = (r32(m, b + 8) & 0xFFFFFF) << 2
        if nxt <= b:
            break
        b = nxt
    top = sorted(per.items(), key=lambda kv: -kv[1][0])[:8]
    print(f"{'  ' * depth}Partition 0x{part:08X}: belegt {used / 1048576:.2f} MB/{nu}, frei {free / 1048576:.2f} MB/{nf} "
          f"(größter {largest} B), Unterpartitionen {len(subs)}")
    print(f"{'  ' * depth}  Halter: " + ' '.join(f'pc=0x{pc:08X}:{v[0] / 1048576:.2f}MB/{v[1]}' for pc, v in top))
    for sp in subs:
        if ((r32(m, sp - 0x24) & 0xFFFFFF) << 2) >= 0x40000 or sp == 0x01625F44:
            walk(m, sp, depth + 1)
    return used


def census(m, part, want, depth=0, acc=None):
    """Alle Blöcke im ganzen Baum: Anzahl/Bytes je gesuchtem Halter-PC, plus Unterpartitionen nach Ersteller-PC."""
    acc = acc if acc is not None else {'pc': defaultdict(lambda: [0, 0]), 'ptn': []}
    if depth > 8 or part >= RAM:
        return acc
    b = (r32(m, part + 8) & 0xFFFFFF) << 2
    guard = 0
    while b and b < RAM and guard < 400000:
        guard += 1
        wc = r32(m, b + 0xC)
        size = (r32(m, b) & 0xFFFFFF) << 2
        if (wc >> 24) & 1:
            pc = (r32(m, b + 0x18) & 0xFFFFFF) << 2
            if pc in want:
                acc['pc'][pc][0] += 1; acc['pc'][pc][1] += size
            if (wc >> 25) & 1:
                if pc in want:
                    acc['ptn'].append((b + 0x24, size, pc))
                census(m, b + 0x24, want, depth + 1, acc)
        nxt = (r32(m, b + 8) & 0xFFFFFF) << 2
        if nxt <= b:
            break
        b = nxt
    return acc


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    want = set()
    args = []
    for a in sys.argv[1:]:
        if a.startswith('--pc='):
            want |= {int(x, 16) for x in a[5:].split(',')}
        else:
            args.append(a)
    for path in args:
        print(f'===== {path}')
        m = load(path)
        walk(m, ROOT)
        if want:
            acc = census(m, ROOT, want)
            for pc in sorted(want):
                n, by = acc['pc'].get(pc, [0, 0])
                print(f'--- Halter pc=0x{pc:08X}: {n} Blöcke, {by / 1048576:.3f} MB (gesamter Baum)')
            for pt, size, pc in acc['ptn']:
                print(f'--- Partition 0x{pt:08X} ({size} B, Ersteller 0x{pc:08X}):')
                walk(m, pt, 1)
        pool = 0x01625F44
        if r32(m, pool + 8):
            print('--- Partikel-Pool (CreateGeoDispObj, initParticleObject):')
            walk(m, pool, 1)
        cls = 0x01202E50
        print(f'--- Fußstaub-Klasse 0x{cls:08X}: flags={m[cls]:02X} {m[cls + 1]:02X}  +0x90=({rf(m, cls + 0x90):g} {rf(m, cls + 0x94):g} '
              f'{rf(m, cls + 0x98):g} {rf(m, cls + 0x9C):g})  Konstante 0x1DC690=({rf(m, 0x1DC690):g} {rf(m, 0x1DC694):g} '
              f'{rf(m, 0x1DC698):g} {rf(m, 0x1DC69C):g})')


if __name__ == '__main__':
    main()

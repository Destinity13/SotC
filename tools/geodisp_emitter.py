#!/usr/bin/env python3
"""Partikel-GeoDisp-Objekte und ihre Emitter-Liste (Klasse +0xE8) auflisten; leer -> auto_end_check löscht das Sub-Link.
  python3 -I tools/geodisp_emitter.py <abbild> <ersteller-pc-hex: Port 10E90B4 / PCSX2 11E3434> [anzahl]"""
import struct, sys
from collections import Counter
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
from ueberlebende import bloecke
m = P.load(sys.argv[1]); crt = int(sys.argv[2], 16); k = int(sys.argv[3]) if len(sys.argv) > 3 else 4
bl = []; bloecke(m, P.ROOT, bl)
parts = {b + 0x24 for b, s, pc, part, d in bl if pc == crt}
objs = []
for b, s, pc, part, d in bl:
    if part in parts:
        o = b + 0x20
        if m[o + 8] == 0x24: objs.append(o)
leer = 0; stat = Counter(); beispiele = []
for o in objs:
    cls = o + 0x20; e = P.r32(m, cls + 0xE8)
    if not e: leer += 1; continue
    n = 0; x = e
    while x and n < 50: n += 1; x = P.r32(m, x + 0x0)  # Annahme: verkettete Liste über +0
    stat[n] += 1
    if len(beispiele) < k: beispiele.append((o, e))
print(f'GeoDisp-Objekte: {len(objs)}, Emitterliste leer: {leer}, Listenlängen: {sorted(stat.items())[:10]}')
for o, e in beispiele:
    w = struct.unpack_from('<96I', m, e & (P.RAM - 1))
    f = struct.unpack_from('<96f', m, e & (P.RAM - 1))
    print(f' GeoDisp 0x{o:08X} Emitter 0x{e:08X}')
    for r in range(0, 96, 8):
        print('   +%03X ' % (r * 4) + ' '.join(f'{w[r+i]:08X}' for i in range(8)) + '   ' + ' '.join(f'{f[r+i]:.4g}' if 1e-6 < abs(f[r+i]) < 1e7 else '.' for i in range(8)))

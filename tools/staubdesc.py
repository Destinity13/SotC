#!/usr/bin/env python3
"""Fußstaub-Beschreibung vergleichen: desc (0x54 B), Definition [desc+0x48] (0x100 B) und Phasen [def+0xD4] (je 0x1BC B, Anzahl [def+0xD0]?).
Zeiger werden relativ zur jeweiligen Basis ausgegeben, damit Port und PCSX2 vergleichbar sind.
  python3 -I tools/staubdesc.py <abbild> <desc-hex>"""
import struct, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
m = P.load(sys.argv[1]); d = int(sys.argv[2], 16)
def dump(name, a, n):
    ws = struct.unpack_from('<%dI' % (n // 4), m, a & (P.RAM - 1))
    print(f'-- {name} @0x{a:08X}')
    for i in range(0, len(ws), 8):
        row = []
        for x in ws[i:i + 8]:
            row.append('P' if 0x00100000 <= x < 0x02000000 and (x & 3) == 0 and x > 0x01000000 else f'{x:08X}')
        print(f'  +{i*4:03X} ' + ' '.join(row))
dump('desc', d, 0x54)
df = P.r32(m, d + 0x48); dump('def', df, 0x100)
ph = P.r32(m, df + 0xD4)
if 0x100000 <= ph < P.RAM:
    for k in range(2): dump(f'phase{k}', ph + k * 0x1BC, 0x1BC)

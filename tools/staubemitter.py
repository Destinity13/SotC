#!/usr/bin/env python3
"""Alle Emitter mit gegebener Beschreibung (obj+0 == desc, E = obj+0x10) im Abbild: Emissionszustand.
  python3 -I tools/staubemitter.py <abbild> <desc-hex>"""
import struct, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
m = P.load(sys.argv[1]); d = int(sys.argv[2], 16)
w = struct.unpack_from('<%dI' % (P.RAM // 4), m)
f = lambda a: struct.unpack_from('<f', m, a)[0]
n = 0
for i in range(0, len(w) - 0x80, 4):
    if w[i] != d: continue
    o = i * 4; E = o + 0x10
    if P.r32(m, E + 0x150) not in (0x4B189680,) and not (0 < abs(f(E + 0x150)) < 1e8): continue
    print(f'o=0x{o:08X} t={f(E+0x14):.4g} +114={P.r32(m,E+0x114)} +C4={P.r32(m,E+0xC4)} +11C={f(E+0x11C):.5g} +120={P.r32(m,E+0x120)} +124={P.r32(m,E+0x124)} '
          f'+150={f(E+0x150):.4g} +154={f(E+0x154):.4g} +158={P.r32(m,E+0x158)} +118={P.r32(m,E+0x118)} +190={P.r32(m,E+0x190)}')
    n += 1
    if n >= 25: break
print('anzahl', n)

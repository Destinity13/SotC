#!/usr/bin/env python3
"""Emitter-Objekte (0x1C0-Blöcke, Halter Port 0x0107DFC0 / PCSX2 0x01178340) mit Kernfeldern auflisten.
  python3 -I tools/emitter_felder.py <abbild> <halter-hex> [codeverschiebung-hex für Callback-Anzeige]"""
import struct, sys
from collections import Counter
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
from ueberlebende import bloecke
m = P.load(sys.argv[1]); pc = int(sys.argv[2], 16); sh = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0
bl = []; bloecke(m, P.ROOT, bl)
ems = [b + 0x20 for b, s, p, part, d in bl if p == pc]
f = lambda a: struct.unpack_from('<f', m, a & (P.RAM - 1))[0]
st = Counter()
rows = []
for o in ems:
    e = o + 0x10
    desc = P.r32(m, o); cb = P.r32(m, e + 0x15C); fl = P.r32(m, e + 0x158); nxt = P.r32(m, o + 0x1B0)
    key = (fl, hex(cb - sh if cb >= 0x01000000 else cb), P.r32(m, e + 0x114), P.r32(m, e + 0x168))
    st[key] += 1
    rows.append((o, desc, f(e + 0x14), f(e + 0x150), f(e + 0x154), fl, cb, P.r32(m, e + 0x114), P.r32(m, e + 0x118), P.r32(m, e + 0x168), f(e + 0x16C), nxt))
print(f'Emitter: {len(ems)}')
print('(+158 flag, +15C callback[Port-Adr], +114 ende, +168):', st.most_common(10))
for r in rows[:12]:
    print(' o=%08X desc=%08X t=%.4g lim=%.4g/%.4g fl158=%u cb=%08X ende114=%u 118=%u 168=%u 16C=%.4g next=%08X' % r)

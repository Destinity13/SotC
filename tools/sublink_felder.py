#!/usr/bin/env python3
"""Felder der Sub-Link-Objekte (Halter 0x011104E8 Port / +0xFA380 PCSX2) ausgeben: überlebende vs. neue.
  python3 -I tools/sublink_felder.py <alt> <neu> <halter-hex> [anzahl]"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
from ueberlebende import bloecke
alt, neu, pc = P.load(sys.argv[1]), P.load(sys.argv[2]), int(sys.argv[3], 16)
k = int(sys.argv[4]) if len(sys.argv) > 4 else 6
ba, bn = [], []; bloecke(alt, P.ROOT, ba); bloecke(neu, P.ROOT, bn)
sa = {b for b, s, p, *_ in ba if p == pc}
for tag, sel in (('ueberlebt', [x for x in bn if x[2] == pc and x[0] in sa]), ('neu', [x for x in bn if x[2] == pc and x[0] not in sa])):
    print(f'== {tag}: {len(sel)}')
    for b, s, p, part, d in sel[:k]:
        o = b + 0x20
        w = [P.r32(neu, o + 4 * i) for i in range(min(s, 0xC0) // 4)]
        print(f' 0x{o:08X} ' + ' '.join(f'{x:08X}' for x in w))
        # Löschinfo bei +0x1C: +0xC Funktion, +0x10 Argument -> Argument-Struktur zeigen
        arg = P.r32(neu, o + 0x1C + 0x10)
        if 0x100000 <= arg < P.RAM:
            aw = [P.r32(neu, arg + 4 * i) for i in range(8)]
            print(f'     arg 0x{arg:08X}: ' + ' '.join(f'{x:08X}' for x in aw))

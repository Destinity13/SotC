#!/usr/bin/env python3
"""Fußstaub-Effekttabelle suchen: Einträge à 0xC Byte {u32 Typ=2, u32 Beschreibung, u8 Modus}, zwei Füße hintereinander
mit Beschreibungsabstand 0x54. Gibt Adresse, Beschreibungen und Modus-Bytes aus.
  python3 -I tools/staubtabelle.py <abbild> [...]"""
import struct, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
for path in sys.argv[1:]:
    m = P.load(path); w = struct.unpack_from('<%dI' % (P.RAM // 4), m); n = 0
    print('==', path)
    for i in range(0x100000 // 4, len(w) - 6):
        if w[i] == 2 and w[i + 3] == 2 and w[i + 4] - w[i + 1] == 0x54 and 0x100000 <= w[i + 1] < P.RAM:
            a = i * 4
            print(f'  0x{a:08X}: desc 0x{w[i+1]:08X}/0x{w[i+4]:08X} modus {m[a+8]}/{m[a+0x14]}  roh ' + ' '.join(f'{x:08X}' for x in w[i:i + 9]))
            n += 1
            if n > 12: break

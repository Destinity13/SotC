#!/usr/bin/env python3
"""Partikel-Zeittakte (Zeiger 0x1196A48 zeigt auf einen davon): Port 0x1226398/0x12263A8/0x12263B8, PCSX2 +0xFA380.
  python3 -I tools/ptakt.py <abbild> <verschiebung-hex 0 oder FA380> ..."""
import struct, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
for i in range(1, len(sys.argv), 2):
    m = P.load(sys.argv[i]); s = int(sys.argv[i + 1], 16)
    out = []
    for a in (0x1226390, 0x1226398, 0x12263A8, 0x12263B8):
        out.append(f'0x{a:07X}: ' + ' '.join(f'{struct.unpack_from("<f", m, a + s + 4*j)[0]:.6g}' for j in range(4)))
    print(sys.argv[i], f'zeiger6A48=0x{P.r32(m, 0x1196A48 + s):08X}', ' | '.join(out))

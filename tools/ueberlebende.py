#!/usr/bin/env python3
"""Lecksuche: Blöcke eines Halter-PCs, die zwischen zwei RAM-Abbildern/Savestates überleben, und wer auf sie zeigt.
  python3 -I tools/ueberlebende.py <alt> <neu> <halter-pc-hex> [ppc-hex …]
"""
import bisect, struct, sys
from collections import Counter, defaultdict
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P

def bloecke(m, part, out, depth=0):
    if depth > 8: return
    b = (P.r32(m, part + 8) & 0xFFFFFF) << 2; g = 0
    while b and b < P.RAM and g < 400000:
        g += 1; wc = P.r32(m, b + 0xC); size = (P.r32(m, b) & 0xFFFFFF) << 2
        if (wc >> 24) & 1:
            pc = (P.r32(m, b + 0x18) & 0xFFFFFF) << 2
            out.append((b, size, pc, part, depth))
            if (wc >> 25) & 1: bloecke(m, b + 0x24, out, depth + 1)
        n = (P.r32(m, b + 8) & 0xFFFFFF) << 2
        if n <= b: break
        b = n

def main():
    alt, neu, pc = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
    ma, mn = P.load(alt), P.load(neu)
    ba, bn = [], []
    bloecke(ma, P.ROOT, ba); bloecke(mn, P.ROOT, bn)
    sa = {(b, s) for b, s, p, *_ in ba if p == pc}
    zn = [(b, s, part, d) for b, s, p, part, d in bn if p == pc]
    ueb = [z for z in zn if (z[0], z[1]) in sa]
    print(f'Halter 0x{pc:08X}: alt {len(sa)}, neu {len(zn)}, überlebt {len(ueb)}, neu hinzu {len(zn) - len(ueb)}')
    print('  in Partition (Tiefe):', Counter((hex(part), d) for b, s, part, d in zn).most_common(6))
    # Rückverweise: wer zeigt auf Nutzdaten (b+0x20) oder Partitionskopf (b+0x24)?
    starts = sorted((b, b + 0x20 + s, p) for b, s, p, *_ in bn)
    keys = [x[0] for x in starts]
    ziele = {}
    for b, s, part, d in zn:
        for off in (0x20, 0x24): ziele[b + off] = b
    words = struct.unpack_from('<%dI' % (P.RAM // 4), mn)
    wer = Counter(); bsp = defaultdict(list)
    for i, w in enumerate(words):
        if w in ziele:
            a = i * 4; k = bisect.bisect_right(keys, a) - 1
            hp = starts[k][2] if k >= 0 and a < starts[k][1] else 0
            holder_blk = starts[k][0] if hp else 0
            off = a - holder_blk - 0x20 if hp else a
            neu_flag = 'neu' if (ziele[w], ) and (ziele[w], next(s for b2, s, *_ in zn if b2 == ziele[w])) not in sa else 'alt'
            wer[(hp, off, neu_flag)] += 1
            if len(bsp[hp]) < 3: bsp[hp].append(f'0x{a:08X}->0x{w:08X}')
    print('Rückverweise (Halter-PC des zeigenden Blocks, Offset, Ziel alt/neu): Anzahl')
    for (hp, off, f), n in wer.most_common(25):
        print(f'  pc=0x{hp:08X} +0x{off:X} [{f}]: {n}   z.B. {bsp[hp][:2]}')

if __name__ == '__main__':
    main()

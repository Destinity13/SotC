#!/usr/bin/env python3
"""Find functions in the stripped loader ELF that reference given strings (lui/addiu|ori pairs)."""
import sys, struct
from elftools.elf.elffile import ELFFile
e = ELFFile(open(sys.argv[1], 'rb'))
mem = {}
for s in e.iter_sections():
    if s['sh_flags'] & 2 and s['sh_type'] != 'SHT_NOBITS' and s['sh_size']:
        mem[s.name] = (s['sh_addr'], s.data())
tbase, text = mem['.text']
def find_str(sub):
    for n, (b, d) in mem.items():
        i = d.find(sub)
        if i >= 0:
            j = d.rfind(b'\0', 0, i) + 1
            return b + j
def func_start(addr):
    # walk back to the 'addiu sp,sp,-N' prologue after a 'jr ra'
    i = (addr - tbase) // 4
    while i > 0:
        w = struct.unpack_from('<I', text, 4 * i)[0]
        prev = struct.unpack_from('<I', text, 4 * (i - 2))[0] if i >= 2 else 0
        if (w >> 16) == 0x27bd and (w & 0x8000) and prev == 0x03e00008:
            return tbase + 4 * i
        i -= 1
    return None
for sub in sys.argv[2:]:
    sa = find_str(sub.encode())
    if sa is None: print(sub, 'not found'); continue
    hi, lo = (sa + 0x8000) >> 16, sa & 0xffff
    refs = set()
    n = len(text) // 4
    for i in range(n):
        w = struct.unpack_from('<I', text, 4 * i)[0]
        if w >> 26 == 0xf and (w & 0xffff) == hi:          # lui rt, hi
            rt = (w >> 16) & 31
            for k in range(1, 12):
                if i + k >= n: break
                w2 = struct.unpack_from('<I', text, 4 * (i + k))[0]
                if w2 >> 26 in (9, 0xd) and (w2 >> 21) & 31 == rt and (w2 & 0xffff) == lo:
                    refs.add(func_start(tbase + 4 * i)); break
    print(f'{sub!r} @ {sa:#x}: functions', sorted(hex(r) for r in refs if r))

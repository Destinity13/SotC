#!/usr/bin/env python3
"""Find which functions reference a string (lui + addiu/ori pair) in a MIPS ELF.
Uses the symbol table for function names when present, else a prologue heuristic.
Usage: str_xrefs.py <elf> <substring>..."""
import bisect, struct, sys
from elftools.elf.elffile import ELFFile
e = ELFFile(open(sys.argv[1], 'rb'))
secs = [(s['sh_addr'], s.data(), s['sh_flags'] & 4) for s in e.iter_sections()
        if s['sh_flags'] & 2 and s['sh_type'] != 'SHT_NOBITS' and s['sh_size']]
funcs = []
st = e.get_section_by_name('.symtab')
if st:
    funcs = sorted((s['st_value'], s.name) for s in st.iter_symbols() if s['st_info']['type'] == 'STT_FUNC')
fa = [f[0] for f in funcs]
def owner(addr, base, text):
    k = bisect.bisect_right(fa, addr) - 1
    if k >= 0 and addr - fa[k] < 0x4000:
        return f'{funcs[k][1]}@{fa[k]:#x}'
    i = (addr - base) // 4
    while i > 1:
        w, p = struct.unpack_from('<II', text, 4 * (i - 2))[1], struct.unpack_from('<I', text, 4 * (i - 2))[0]
        if (w >> 16) == 0x27bd and (w & 0x8000) and p == 0x03e00008:
            return f'sub@{base + 4 * i:#x}'
        i -= 1
    return '?'
for sub in sys.argv[2:]:
    hits = []
    for b, d, _ in secs:
        i = d.find(sub.encode())
        while i >= 0:
            hits.append(b + d.rfind(b'\0', 0, i) + 1); i = d.find(sub.encode(), i + 1)
    for sa in hits:
        hi, lo = ((sa + 0x8000) >> 16) & 0xffff, sa & 0xffff
        refs = set()
        for b, text, x in secs:
            if not x: continue
            n = len(text) // 4
            for i in range(n):
                w = struct.unpack_from('<I', text, 4 * i)[0]
                if w >> 26 == 0xf and (w & 0xffff) == hi:
                    rt = (w >> 16) & 31
                    for k in range(1, 16):
                        if i + k >= n: break
                        w2 = struct.unpack_from('<I', text, 4 * (i + k))[0]
                        if w2 >> 26 in (9, 0xd) and (w2 >> 21) & 31 == rt and (w2 & 0xffff) == lo:
                            refs.add(owner(b + 4 * i, b, text)); break
        print(f'{sub!r} @ {sa:#x}: {sorted(refs)}')

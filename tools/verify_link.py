#!/usr/bin/env python3
"""Sanity-check a linked SotC ELF: every JAL in the module code must land on a known function."""
import sys, struct, collections
from elftools.elf.elffile import ELFFile
e = ELFFile(open(sys.argv[1], 'rb'))
funcs, ext = set(), {}
for s in e.get_section_by_name('.symtab').iter_symbols():
    if s['st_info']['type'] == 'STT_FUNC': funcs.add(s['st_value'])
    if s.name.startswith('__xff_extern__'): ext[s['st_value']] = s.name[14:]
c = collections.Counter(); bad = []
for sec in e.iter_sections():
    if not (sec['sh_flags'] & 4) or sec['sh_addr'] < 0x200000: continue
    d, base = sec.data(), sec['sh_addr']
    for i in range(0, len(d) - 3, 4):
        w = struct.unpack_from('<I', d, i)[0]
        if w >> 26 == 3:
            t = ((base + i) & 0xf0000000) | ((w & 0x3ffffff) << 2)
            if t in funcs: c['func'] += 1
            elif t in ext: c['extern:' + ext[t].split('_')[0]] += 1
            else: c['unknown'] += 1; bad.append(hex(base + i))
print(dict(c)); print('first unknown sites:', bad[:10])

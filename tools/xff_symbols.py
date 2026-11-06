#!/usr/bin/env python3
"""Summarise symbols of the converted XFF ELF objects and resolve cross-module imports."""
import sys, collections
from elftools.elf.elffile import ELFFile

mods = {}
for path in sys.argv[1:]:
    name = path.rsplit('/', 1)[-1].split('.')[0]
    with open(path, 'rb') as f:
        e = ELFFile(f); st = e.get_section_by_name('.symtab')
        d, u, funcs = {}, set(), 0
        for s in st.iter_symbols():
            if not s.name: continue
            if s['st_shndx'] == 'SHN_UNDEF': u.add(s.name)
            else:
                d[s.name] = s['st_info']['type']
                funcs += s['st_info']['type'] == 'STT_FUNC'
        mods[name] = (d, u, funcs)
for n, (d, u, funcs) in mods.items():
    src = collections.Counter()
    unresolved = []
    for s in u:
        owners = [m for m, (dd, _, _) in mods.items() if s in dd]
        if owners: src[owners[0]] += 1
        else: unresolved.append(s)
    print(f"{n}: {len(d)} defined ({funcs} functions), {len(u)} imports -> {dict(src)}, unresolved {len(unresolved)}")
    print("   unresolved sample:", sorted(unresolved)[:25])

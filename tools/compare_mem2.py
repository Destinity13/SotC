#!/usr/bin/env python3
"""Self-test: linked ELF vs. RAM image produced by the game's own loader (emulated).
Ignores words the emulator replaced by nops (R5900-only ops) and classifies the rest
against the dynamic data-reference sites."""
import json, struct, sys, collections
sys.path.insert(0, sys.path[0])
from elftools.elf.elffile import ELFFile
from r5900_ext import R5900
mem = open(sys.argv[2], 'rb').read()
dyn = {int(s['addr'], 16) for s in json.load(open(sys.argv[3]))['sites']}
e = ELFFile(open(sys.argv[1], 'rb'))
cls = R5900.classify
total = collections.Counter()
for s in e.iter_sections():
    if s['sh_type'] == 'SHT_NOBITS' or not any(s.name.endswith('.' + m) for m in ('KERNEL', 'MANAGER', 'GAMECORE')): continue
    a0, d = s['sh_addr'], s.data(); c = collections.Counter(); ex = []
    for i in range(0, len(d) - 3, 4):
        lw, mw = struct.unpack_from('<I', d, i)[0], struct.unpack_from('<I', mem, a0 + i)[0]
        if lw == mw: continue
        a = a0 + i
        if mw == 0 and cls(None, lw): c['R5900-nop (Emulator)'] += 1
        elif a in dyn: c['dynamische Stelle'] += 1
        else:
            c['ANDERE'] += 1
            if len(ex) < 5: ex.append(f'{a:#x}: linked {lw:#010x} mem {mw:#010x}')
    total.update(c)
    print(f'{s.name:18s} {dict(c) or "identisch"}' + (f'  Beispiele: {ex}' if ex else ''))
print('GESAMT', dict(total))

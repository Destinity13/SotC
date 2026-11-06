#!/usr/bin/env python3
"""Find J/JAL targets that land inside other functions (shared tails etc.) and are therefore
missing from PS2Recomp's function table, and add them as entry points to the config.
Run after a first recompilation (needs output/register_functions.cpp).
Usage: find_missing_targets.py <linked.elf> <register_functions.cpp> <config.toml>"""
import re, struct, sys
from elftools.elf.elffile import ELFFile
elf, reg, cfg = sys.argv[1:4]
table = set(int(m, 16) for m in re.findall(r'// 0x([0-9a-f]+)\n', open(reg).read()))
e = ELFFile(open(elf, 'rb'))
code = [(s['sh_addr'], s.data()) for s in e.iter_sections() if s['sh_flags'] & 4 and not s.name.startswith('.vutext')]
incode = lambda a: any(b <= a < b + len(d) for b, d in code)
missing = set()
for b, d in code:
    for i in range(0, len(d) - 3, 4):
        w = struct.unpack_from('<I', d, i)[0]
        if w >> 26 in (2, 3):
            t = ((b + i) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
            if t not in table and incode(t):
                missing.add(t)
text = open(cfg).read()
have = set(int(x, 16) for x in re.findall(r'"(?:[^"@]*@)?(0x[0-9A-Fa-f]+)"', text.split('entry_points = [', 1)[1].split(']', 1)[0])) if 'entry_points = [' in text else set()
new = sorted(missing - have)
if new:
    ins = ''.join(f'  "0x{t:08X}",\n' for t in new)
    text = text.replace('entry_points = [\n', 'entry_points = [\n' + ins, 1) if 'entry_points = [\n' in text else text + '\n[general.extra]\n'
    open(cfg, 'w').write(text)
print(f'{len(missing)} missing J/JAL targets, {len(new)} added as entry points')

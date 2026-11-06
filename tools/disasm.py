#!/usr/bin/env python3
"""Disassemble one function of a MIPS ELF: disasm.py <elf> <addr> [max_insns]. Stops after jr ra + delay slot."""
import sys
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS64, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile
e = ELFFile(open(sys.argv[1], 'rb')); addr = int(sys.argv[2], 0); mx = int(sys.argv[3]) if len(sys.argv) > 3 else 400
syms = {}
st = e.get_section_by_name('.symtab')
if st:
    for s in st.iter_symbols():
        if s.name and s['st_value']: syms.setdefault(s['st_value'], s.name)
for s in e.iter_sections():
    if s['sh_flags'] & 4 and s['sh_addr'] <= addr < s['sh_addr'] + s['sh_size']:
        d = s.data()[addr - s['sh_addr']:]
        md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 + CS_MODE_LITTLE_ENDIAN)
        end = None
        for n, i in enumerate(md.disasm(d, addr)):
            t = ''
            if i.mnemonic in ('jal', 'j') :
                tgt = int(i.op_str, 0); t = '  ; ' + syms.get(tgt, '')
            print(f'{i.address:08x}: {i.mnemonic:8s} {i.op_str}{t}')
            if end is not None and i.address >= end: break
            if i.mnemonic == 'jr' and i.op_str == '$ra': end = i.address + 4
            if n >= mx: break

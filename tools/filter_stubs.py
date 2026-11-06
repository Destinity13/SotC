#!/usr/bin/env python3
"""Keep only analyzer stubs that the PS2Recomp runtime really implements.
Stubs whose runtime handler is just TODO_NAMED (throws) are removed, so the game's own
recompiled code runs instead.  Also drops stubs whose address carries a *different* function name in the ELF symbol table
(the analyzer matches library functions by code pattern and can misidentify game code,
e.g. KERNEL isysObjSignalSema as libc __malloc_unlock).
Usage: filter_stubs.py <config.toml> <ps2xRuntime dir> [linked.elf]"""
import glob, os, re, sys
cfg, rt = sys.argv[1], sys.argv[2]
byaddr = {}
if len(sys.argv) > 3:
    from elftools.elf.elffile import ELFFile
    _e = ELFFile(open(sys.argv[3], 'rb'))
    for _s in _e.get_section_by_name('.symtab').iter_symbols():
        if _s['st_info']['type'] == 'STT_FUNC' and _s['st_value']:
            byaddr.setdefault(_s['st_value'], set()).add(_s.name.lstrip('_'))
src = ''
for f in glob.glob(os.path.join(rt, 'src', 'lib', 'Kernel', '**', '*.cpp'), recursive=True):
    src += open(f, errors='ignore').read() + '\n'
calls = open(os.path.join(rt, 'include', 'ps2_call_list.h')).read()
known = set(re.findall(r'X\((\w+)\)', calls))
todo = set()
for m in re.finditer(r'void\s+(\w+)\s*\(uint8_t\s*\*\s*rdram[^)]*\)\s*\{(.*?)\n    \}', src, re.S):
    name, body = m.group(1), m.group(2)
    if 'TODO_NAMED' in body or re.search(r'^\s*TODO\(', body, re.M):
        todo.add(name)
text = open(cfg).read()
m = re.search(r'^stubs = \[\n(.*?)^\]', text, re.S | re.M)
entries = re.findall(r'"([^"]+)"', m.group(1))
keep, drop = [], []
for e in entries:
    nm = e.split('@')[0].lstrip('_') if e.split('@')[0] not in known else e.split('@')[0]
    base = e.split('@')[0]
    ok = (base in known and base not in todo) or (base.lstrip('_') in known and base.lstrip('_') not in todo and base.startswith('_'))
    if ok and '@' in e and byaddr:
        names = byaddr.get(int(e.split('@')[1], 16))
        if names and base.lstrip('_') not in names:
            ok = False
    (keep if ok else drop).append(e)
new = 'stubs = [\n' + ''.join(f'  "{e}",\n' for e in keep) + ']'
text = text[:m.start()] + new + text[m.end():]
text += '\n# filter_stubs.py removed (game code runs instead): ' + ', '.join(d.split('@')[0] for d in drop) + '\n'
open(cfg, 'w').write(text)
print(f'kept {len(keep)} stubs, removed {len(drop)} (unimplemented in runtime): {[d.split("@")[0] for d in drop][:40]}')

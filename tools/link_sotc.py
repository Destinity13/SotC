#!/usr/bin/env python3
"""Static linker for Shadow of the Colossus (PS2).

Takes the loader ELF (SCES_533.26 / SCUS_974.72) plus the KERNEL, MANAGER and
GAMECORE modules (already converted from xff2 to ELF relocatable objects with
third_party/sotc-decomp/tools/xff2.py) and produces ONE fixed-address
ET_EXEC ELF that static recompilers such as PS2Recomp can consume.

In the real game the loader places the XFF modules at heap addresses chosen at
runtime and resolves their relocations itself. Here every module gets a fixed
address and the relocations (R_MIPS_32/26/HI16/LO16) are applied ahead of time.

Data-table XFFs from the archive (found by tools/scan_archive_xff.py) can be
linked in with --data-dir. Symbols that still no module defines (mostly debug
functions stripped from retail) are given placeholder slots in an `.xff_extern` region and written to the layout
JSON. Code referencing them recompiles fine but these values must be resolved
before the game can run correctly (see docs/NEXT_STEPS.md).

Usage:
    link_sotc.py --loader extracted/SCES_533.26 \
        --module elf/KERNEL.o --module elf/MANAGER.o --module elf/GAMECORE.o \
        --out elf/sotc_linked.elf --layout elf/sotc_layout.json [--base 0x200000]
"""
import argparse, json, struct, sys
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16 = 2, 4, 5, 6
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4
PT_LOAD, PF_X, PF_W, PF_R = 1, 1, 2, 4
SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_NOBITS = 1, 2, 3, 8
EXTERN_SLOT = 0x10


def align(v, a):
    a = max(a, 1)
    return (v + a - 1) & ~(a - 1)


def sext16(v):
    v &= 0xffff
    return v - 0x10000 if v & 0x8000 else v


class OutSection:
    def __init__(self, name, addr, data, size, flags, nobits, align_, origin):
        self.name, self.addr, self.data, self.size = name, addr, data, size
        self.flags, self.nobits, self.align, self.origin = flags, nobits, align_, origin


class Module:
    def __init__(self, path, align_=0x1000, kind='code'):
        self.path, self.align, self.kind = path, align_, kind
        self.name = path.replace('\\', '/').rsplit('/', 1)[-1].split('.')[0]
        self.f = open(path, 'rb')
        self.elf = ELFFile(self.f)
        self.symtab = self.elf.get_section_by_name('.symtab')
        self.base = {}          # section index -> absolute address
        self.data = {}          # section index -> bytearray (alloc PROGBITS)
        self.out = {}           # section index -> OutSection


def place_modules(mods, base, placement=None):
    """Sequential layout from `base`, unless `placement` (from emu_loader.py / the game's
    own loader log) pins a module's sections to the addresses the real loader uses."""
    placement = placement or {}
    pinned_end = 0
    for m in mods:
        for sec in placement.get(m.name, {}).get('sections', {}).values():
            pinned_end = max(pinned_end, int(sec['addr'], 16) + int(sec['size'], 16))
    addr = max(base, pinned_end)
    sections = []
    for m in mods:
        pins = {n: int(v['addr'], 16) for n, v in placement.get(m.name, {}).get('sections', {}).items()}
        if not pins:
            addr = align(addr, m.align)
        m.start = min(pins.values()) if pins else addr
        for idx, s in enumerate(m.elf.iter_sections()):
            if not (s['sh_flags'] & SHF_ALLOC) or s.name == '.reginfo':
                continue
            if pins and s.name not in pins:
                if s['sh_size']:
                    raise SystemExit(f"{m.name}: placement has no address for {s.name}")
                m.base[idx] = m.start
                continue
            if s['sh_size'] == 0:
                m.base[idx] = addr
                continue
            if pins:
                m.base[idx] = pins[s.name]
            else:
                addr = align(addr, max(s['sh_addralign'], 16))
                m.base[idx] = addr
            nobits = s['sh_type'] == 'SHT_NOBITS'
            flags = s['sh_flags']
            if s.name.startswith('.vu'):
                # VU1 microcode/data: DMA'd to the VU, never executed by the EE.
                flags &= ~SHF_EXECINSTR
            data = None if nobits else bytearray(s.data())
            if data is not None:
                m.data[idx] = data
            o = OutSection(f"{s.name}.{m.name}", m.base[idx], data, s['sh_size'], flags,
                           nobits, max(s['sh_addralign'], 16), m.name)
            m.out[idx] = o
            sections.append(o)
            if not pins:
                addr += s['sh_size']
        m.end = max(o.addr + o.size for o in m.out.values()) if pins else addr
    # overlap check
    spans = sorted((o.addr, o.addr + o.size, o.name) for o in sections)
    for (a0, e0, n0), (a1, e1, n1) in zip(spans, spans[1:]):
        if a1 < e0:
            raise SystemExit(f"overlap: {n0} [{a0:#x},{e0:#x}) and {n1} [{a1:#x},{e1:#x})")
    return sections, max([addr] + [m.end for m in mods])


def build_globals(mods):
    glob, dup = {}, []
    for m in mods:
        for s in m.symtab.iter_symbols():
            if not s.name or s['st_shndx'] in ('SHN_UNDEF',):
                continue
            if s['st_info']['bind'] not in ('STB_GLOBAL', 'STB_WEAK'):
                continue
            v = sym_addr(m, s)
            if v is None:
                continue
            if s.name in glob:
                dup.append((s.name, glob[s.name][1], m.name))
                continue
            glob[s.name] = (v, m.name, s['st_info']['type'], s['st_size'])
    return glob, dup


def sym_addr(m, s):
    sh = s['st_shndx']
    if sh == 'SHN_ABS':
        return s['st_value']
    if isinstance(sh, int) and sh in m.base:
        return m.base[sh] + s['st_value']
    return None


def find_extern_functions(mods, glob):
    """Undefined symbols that are JAL targets: they get real 'return 0' stubs."""
    out = []
    for m in mods:
        for rs in m.elf.iter_sections():
            if not isinstance(rs, RelocationSection):
                continue
            for r in rs.iter_relocations():
                if r['r_info_type'] != R_MIPS_26:
                    continue
                s = m.symtab.get_symbol(r['r_info_sym'])
                if s['st_shndx'] == 'SHN_UNDEF' and s.name not in glob and s.name not in out:
                    out.append(s.name)
    return sorted(out)


STUB_CODE = struct.pack('<II', 0x03e00008, 0x00001025)  # jr $ra ; move $v0,$zero


def link(mods, glob, extern_base, stub_addrs=None):
    externs = {}
    next_ext = [extern_base]
    for n, ad in (stub_addrs or {}).items():
        externs[n] = {'addr': ad, 'used_by': set(), 'reloc_types': set(), 'stub': True}
    stats = {'relocs': 0, 'orphan_lo16': 0, 'jal_out_of_region': 0}
    sites = []
    taken = set()          # absolute values formed by R_MIPS_32 / HI16+LO16 (candidate function pointers)

    def resolve(m, symidx):
        s = m.symtab.get_symbol(symidx)
        if s['st_shndx'] != 'SHN_UNDEF':
            v = sym_addr(m, s)
            if v is None:
                raise RuntimeError(f"{m.name}: symbol {s.name!r} in unplaced section {s['st_shndx']}")
            return v
        if s.name in glob:
            return glob[s.name][0]
        if s.name not in externs:
            externs[s.name] = {'addr': next_ext[0], 'used_by': set(), 'reloc_types': set()}
            next_ext[0] += EXTERN_SLOT
        externs[s.name]['used_by'].add(m.name)
        return externs[s.name]['addr']

    for m in mods:
        for rs in m.elf.iter_sections():
            if not isinstance(rs, RelocationSection) or rs.name.startswith('.rela'):
                continue
            tgt = rs['sh_info']
            if tgt not in m.data:
                continue
            buf, base = m.data[tgt], m.base[tgt]
            pending_hi = []   # (offset, symidx, S)
            for r in rs.iter_relocations():
                off, typ, symidx = r['r_offset'], r['r_info_type'], r['r_info_sym']
                S = resolve(m, symidx)
                if not m.symtab.get_symbol(symidx)['st_shndx'] == 'SHN_UNDEF' or True:
                    nm = m.symtab.get_symbol(symidx).name
                    if nm in externs:
                        externs[nm]['reloc_types'].add(typ)
                w = struct.unpack_from('<I', buf, off)[0]
                stats['relocs'] += 1
                nm_ = m.symtab.get_symbol(symidx).name
                if nm_ in externs and not externs[nm_].get('stub'):
                    sites.append((base + off, typ, nm_))
                if typ == R_MIPS_32:
                    w = (w + S) & 0xffffffff
                    taken.add(w)
                elif typ == R_MIPS_26:
                    P = base + off
                    t = (((w & 0x3ffffff) << 2) | (P & 0xf0000000)) + S
                    if (t & 0xf0000000) != (P & 0xf0000000):
                        stats['jal_out_of_region'] += 1
                    w = (w & 0xfc000000) | ((t >> 2) & 0x3ffffff)
                elif typ == R_MIPS_HI16:
                    pending_hi.append((off, symidx, S))
                    continue
                elif typ == R_MIPS_LO16:
                    lo = sext16(w)
                    keep = []
                    for hoff, hsym, hS in pending_hi:
                        if hsym != symidx:
                            keep.append((hoff, hsym, hS))
                            continue
                        hw = struct.unpack_from('<I', buf, hoff)[0]
                        v = ((hw & 0xffff) << 16) + lo + hS
                        taken.add(v & 0xffffffff)
                        hw = (hw & 0xffff0000) | (((v + 0x8000) >> 16) & 0xffff)
                        struct.pack_into('<I', buf, hoff, hw)
                    pending_hi = keep
                    v = lo + S
                    w = (w & 0xffff0000) | (v & 0xffff)
                else:
                    raise RuntimeError(f"{m.name}: unsupported relocation type {typ} at {rs.name}+{off:#x}")
                struct.pack_into('<I', buf, off, w)
            if pending_hi:
                # HI16 without a matching LO16: assume lo == 0.
                stats['orphan_lo16'] += len(pending_hi)
                for hoff, hsym, hS in pending_hi:
                    hw = struct.unpack_from('<I', buf, hoff)[0]
                    v = ((hw & 0xffff) << 16) + hS
                    hw = (hw & 0xffff0000) | (((v + 0x8000) >> 16) & 0xffff)
                    struct.pack_into('<I', buf, hoff, hw)
    link.taken = taken
    return externs, next_ext[0], stats, sites


def loader_sections(path):
    f = open(path, 'rb')
    e = ELFFile(f)
    out = []
    for s in e.iter_sections():
        if not (s['sh_flags'] & SHF_ALLOC) or s['sh_size'] == 0 or s.name == '.reginfo':
            continue
        nobits = s['sh_type'] == 'SHT_NOBITS'
        out.append(OutSection(f"{s.name}.LOADER" if s.name != '.text' else '.text',
                              s['sh_addr'], None if nobits else bytearray(s.data()),
                              s['sh_size'], s['sh_flags'], nobits,
                              max(s['sh_addralign'], 4), 'LOADER'))
    return e.header['e_entry'], e.header['e_flags'], out


def write_elf(path, entry, eflags, sections, symbols):
    # symbols: list of (name, value, size, type, shndx_name)
    sections = sorted(sections, key=lambda s: s.addr)
    shstr = bytearray(b'\0')
    strtab = bytearray(b'\0')

    def addstr(tab, s):
        o = len(tab); tab.extend(s.encode() + b'\0'); return o

    # Segments: one PT_LOAD per contiguous run of same (origin, perms).
    def perms(s):
        p = PF_R
        if s.flags & SHF_WRITE: p |= PF_W
        if s.flags & SHF_EXECINSTR: p |= PF_X
        return p

    segs = []
    for s in sections:
        p = perms(s)
        if segs and segs[-1]['origin'] == s.origin and segs[-1]['perm'] == p and \
           not (segs[-1]['has_nobits'] and not s.nobits):
            segs[-1]['secs'].append(s)
        else:
            segs.append({'origin': s.origin, 'perm': p, 'secs': [s], 'has_nobits': False})
        if s.nobits:
            segs[-1]['has_nobits'] = True

    ehsize, phentsize, shentsize = 52, 32, 40
    off = ehsize + phentsize * len(segs)
    body = bytearray()
    phdrs = []
    for sg in segs:
        first = sg['secs'][0]
        off = align(off, 0x1000) if not body else align(off, 0x10)
        # keep file offset congruent with vaddr modulo 16
        while (off % 16) != (first.addr % 16):
            off += 1
        seg_off = off
        for s in sg['secs']:
            s.offset = seg_off + (s.addr - first.addr)
        filesz = 0
        for s in sg['secs']:
            if not s.nobits:
                filesz = s.addr - first.addr + s.size
        memsz = sg['secs'][-1].addr + sg['secs'][-1].size - first.addr
        phdrs.append((PT_LOAD, seg_off, first.addr, first.addr, filesz, memsz, sg['perm'], 0x10))
        sg['file'] = (seg_off, filesz)
        off = seg_off + filesz

    image_end = off
    image = bytearray(image_end)
    for sg in segs:
        for s in sg['secs']:
            if not s.nobits:
                image[s.offset:s.offset + s.size] = s.data
            else:
                s.offset = sg['file'][0] + sg['file'][1]

    # section headers: null, alloc sections..., .symtab, .strtab, .shstrtab
    shdrs = [(0,) * 10]
    sec_index = {}
    for s in sections:
        sec_index[s.name] = len(shdrs)
        shdrs.append((addstr(shstr, s.name), SHT_NOBITS if s.nobits else SHT_PROGBITS,
                      s.flags & 0xffffffff, s.addr, s.offset, s.size, 0, 0, s.align, 0))

    def find_sec(addr):
        for s in sections:
            if s.addr <= addr < s.addr + max(s.size, 1):
                return sec_index[s.name]
        return 0xfff1  # SHN_ABS

    symdata = bytearray(16)
    for name, value, size, styp, _ in sorted(symbols, key=lambda x: x[1]):
        t = {'STT_FUNC': 2, 'STT_OBJECT': 1, 'STT_NOTYPE': 0}.get(styp, 0)
        symdata += struct.pack('<IIIBBH', addstr(strtab, name), value, size,
                               (1 << 4) | t, 0, find_sec(value))
    symoff = align(image_end, 4)
    stroff = symoff + len(symdata)
    symtab_idx = len(shdrs)
    shdrs.append((addstr(shstr, '.symtab'), SHT_SYMTAB, 0, 0, symoff, len(symdata),
                  symtab_idx + 1, 1, 4, 16))
    shdrs.append((addstr(shstr, '.strtab'), SHT_STRTAB, 0, 0, stroff, len(strtab), 0, 0, 1, 0))
    shstr_idx = len(shdrs)
    name_off = addstr(shstr, '.shstrtab')
    shstroff = stroff + len(strtab)
    shdrs.append((name_off, SHT_STRTAB, 0, 0, shstroff, len(shstr), 0, 0, 1, 0))
    shoff = align(shstroff + len(shstr), 4)

    out = bytearray()
    out += b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8)
    out += struct.pack('<HHIIIIIHHHHHH', 2, 8, 1, entry, ehsize, shoff, eflags,
                       ehsize, phentsize, len(phdrs), shentsize, len(shdrs), shstr_idx)
    for ph in phdrs:
        out += struct.pack('<IIIIIIII', *ph)
    image[:len(out)] = out
    image = image[:image_end]
    image += bytes(symoff - len(image)) + symdata + strtab + shstr
    image += bytes(shoff - len(image))
    for sh in shdrs:
        image += struct.pack('<IIIIIIIIII', *sh)
    with open(path, 'wb') as f:
        f.write(image)
    return len(image)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--loader', required=True)
    ap.add_argument('--module', action='append', required=True)
    ap.add_argument('--data-dir', help='directory of data-table XFFs converted to .o (linked after the code modules)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--layout', required=True)
    ap.add_argument('--base', type=lambda x: int(x, 0), default=0x00200000)
    ap.add_argument('--placement', help='JSON with real section addresses per module (tools/emu_loader.py)')
    ap.add_argument('--code-pointers', help='write addresses of code that the modules take as values (function pointers / callbacks)')
    ap.add_argument('--extra-func', action='append', default=[],
                    help='MODULE:SECTION:OFFSET:NAME - add a function symbol the analyzer would miss (only reached via pointers)')
    ap.add_argument('--dynamic-sites', help='write JSON list of code sites that reference runtime-resolved data symbols')
    ap.add_argument('--symbol-owners', help='data_xff/index.json from scan_archive_xff.py (adds the defining container to each site)')
    a = ap.parse_args()

    entry, eflags, lsecs = loader_sections(a.loader)
    loader_end = max(s.addr + s.size for s in lsecs)
    if a.base < loader_end:
        sys.exit(f"--base {a.base:#x} overlaps the loader (ends at {loader_end:#x})")

    mods = [Module(p) for p in a.module]
    if a.data_dir:
        import glob as _glob, os as _os
        for p in sorted(_glob.glob(_os.path.join(a.data_dir, '*.o'))):
            mods.append(Module(p, 0x40, 'data'))
    placement = json.load(open(a.placement)) if a.placement else None
    msecs, end = place_modules(mods, a.base, placement)
    glob, dup = build_globals(mods)
    stub_names = find_extern_functions(mods, glob)
    stub_base = align(end, 0x1000)
    stub_addrs = {n: stub_base + 8 * i for i, n in enumerate(stub_names)}
    stub_sec = OutSection('.xff_stubs', stub_base, bytearray(STUB_CODE * len(stub_names)),
                          8 * len(stub_names), SHF_ALLOC | SHF_EXECINSTR, False, 16, 'STUBS')
    extern_base = align(stub_base + 8 * len(stub_names), 0x1000)
    externs, ext_end, stats, sites = link(mods, glob, extern_base, stub_addrs)

    ext_sec = OutSection('.xff_extern', extern_base, None, max(ext_end - extern_base, 16),
                         SHF_ALLOC | SHF_WRITE, True, 16, 'EXTERN')
    symbols = [(n, v, sz, t, mod) for n, (v, mod, t, sz) in glob.items()]
    # module entry points + local function symbols (prefixed if they clash)
    seen = set(glob)
    for m in mods:
        for s in m.symtab.iter_symbols():
            if not s.name or s['st_info']['bind'] != 'STB_LOCAL' or s['st_info']['type'] not in ('STT_FUNC', 'STT_OBJECT'):
                continue
            v = sym_addr(m, s)
            if v is None:
                continue
            n = s.name if s.name not in seen else f"{m.name}__{s.name}"
            seen.add(n)
            symbols.append((n, v, s['st_size'], s['st_info']['type'], m.name))
    for spec in a.extra_func:
        mname, sname, off, fname = spec.split(':')
        m = next(x for x in mods if x.name == mname)
        idx = next(i for i, sec in enumerate(m.elf.iter_sections()) if sec.name == sname)
        symbols.append((fname, m.base[idx] + int(off, 0), 0, 'STT_FUNC', mname))
    for n, e in externs.items():
        if e.get('stub'):
            symbols.append((n, e['addr'], 8, 'STT_FUNC', 'STUBS'))
        else:
            symbols.append((f"__xff_extern__{n}", e['addr'], EXTERN_SLOT, 'STT_OBJECT', 'EXTERN'))

    secs = lsecs + msecs + ([stub_sec] if stub_names else []) + [ext_sec]
    size = write_elf(a.out, entry, eflags, secs, symbols)

    entries = {}
    for m in mods:
        for nm in ('ioskernel', 'entrymanager', 'gamecore'):
            if nm in glob and glob[nm][1] == m.name:
                entries[m.name] = {'symbol': nm, 'addr': hex(glob[nm][0])}
    layout = {
        'note': 'Fixed-address layout chosen by link_sotc.py; not the addresses the original loader would pick.',
        'entry_loader': hex(entry),
        'base': hex(a.base),
        'modules': {m.name: {'kind': m.kind, 'start': hex(m.start), 'end': hex(m.end),
                             'sections': {o.name: [hex(o.addr), hex(o.size)] for o in m.out.values()}}
                    for m in mods},
        'module_entry_points': entries,
        'extern_region': [hex(extern_base), hex(ext_end)],
        'stats': {**stats, 'globals': len(glob), 'duplicate_globals': len(dup), 'stubs': len(stub_names), 'data_externs': len(externs) - len(stub_names)},
        'duplicate_globals': dup[:200],
        'stub_functions': {n: hex(ad) for n, ad in stub_addrs.items()},
        'externs': {n: {'addr': hex(e['addr']), 'used_by': sorted(e['used_by']),
                        'reloc_types': sorted(e['reloc_types'])}
                    for n, e in sorted(externs.items()) if not e.get('stub')},
    }
    with open(a.layout, 'w') as f:
        json.dump(layout, f, indent=1)
    if a.code_pointers:
        code = [(o.addr, o.addr + o.size) for o in msecs if o.flags & SHF_EXECINSTR]
        ptrs = sorted(v for v in link.taken if v % 4 == 0 and any(lo <= v < hi for lo, hi in code))
        with open(a.code_pointers, 'w') as f:
            f.write('\n'.join(f'0x{v:08X}' for v in ptrs) + '\n')
        print(f"  code pointers: {len(ptrs)} -> {a.code_pointers}")
    if a.dynamic_sites:
        owners = {}
        if a.symbol_owners:
            owners = json.load(open(a.symbol_owners)).get('symbol_owner', {})
        names = {R_MIPS_32: 'R_MIPS_32', R_MIPS_26: 'R_MIPS_26', R_MIPS_HI16: 'R_MIPS_HI16', R_MIPS_LO16: 'R_MIPS_LO16'}
        out = [{'addr': f'{ad:#010x}', 'type': names[t], 'symbol': n, 'container': (owners.get(n) or [None])[0]}
               for ad, t, n in sorted(sites)]
        with open(a.dynamic_sites, 'w') as f:
            json.dump({'note': 'Instructions whose immediate is patched by the game at runtime when data sheets load. '
                               'PS2Recomp must read these immediates from guest memory (see patches/ps2recomp-dynamic-immediates.patch).',
                       'count': len(out), 'sites': out}, f, indent=1)
        print(f"  dynamic sites: {len(out)} -> {a.dynamic_sites}")
    print(f"wrote {a.out} ({size} bytes), entry {entry:#x}")
    code = [m for m in mods if m.kind == 'code']; data = [m for m in mods if m.kind == 'data']
    for m in code:
        print(f"  {m.name:9s} {m.start:#010x}-{m.end:#010x}")
    if data:
        print(f"  data      {data[0].start:#010x}-{data[-1].end:#010x} ({len(data)} tables)")
    print(f"  stubs     {stub_base:#010x} ({len(stub_names)} 'return 0' functions)")
    print(f"  externs   {extern_base:#010x}-{ext_end:#010x} ({len(externs) - len(stub_names)} unresolved data symbols)")
    print(f"  stats: {layout['stats']}")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Scan the SotC data archive (PAL: NICO.DAT + XAB..XAE, one contiguous sector space)
for every embedded xff2 container (validated via its duplicated header pointers),
de-duplicate by content and record which global symbols each defines. Only the
containers needed to resolve the link externs are written out.

Usage: scan_archive_xff.py <extracted_dir> <out_dir> [--externs elf/sotc_layout.json]
Writes <out_dir>/xff_<hash>.xff for needed containers plus <out_dir>/index.json.
"""
import hashlib, json, mmap, os, struct, sys

SECTOR = 2048
PARTS = ['NICO.DAT', 'XAB', 'XAC', 'XAD', 'XAE']


class Archive:
    def __init__(self, root):
        self.parts, off = [], 0
        for p in PARTS:
            path = os.path.join(root, p)
            if not os.path.exists(path):
                break
            f = open(path, 'rb')
            m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            self.parts.append((off, len(m), m, f))
            off += len(m)
        self.size = off

    def read(self, pos, n):
        out = bytearray()
        for base, ln, m, _ in self.parts:
            if pos >= base + ln or n == 0:
                continue
            if pos < base:
                break
            k = min(n, base + ln - pos)
            out += m[pos - base:pos - base + k]
            pos += k; n -= k
        return bytes(out)


def xff_globals(data):
    """Return (defined_globals, undefined) names from an xff2 blob using its header."""
    w = struct.unpack_from('<28I', data, 0)
    nsym, symoff, stroff = w[9], w[21], w[22]
    defs, undef = [], []
    for i in range(nsym):
        name, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', data, symoff + 16 * i)
        if name == 0:
            continue
        end = data.index(b'\0', stroff + name)
        s = data[stroff + name:end].decode('latin1')
        if shndx == 0:
            undef.append(s)
        elif info >> 4 in (1, 2):
            defs.append(s)
    return defs, undef


def main():
    root, out = sys.argv[1], sys.argv[2]
    ext_file = sys.argv[sys.argv.index('--externs') + 1] if '--externs' in sys.argv else None
    os.makedirs(out, exist_ok=True)
    a = Archive(root)
    seen, index, sym_owner, blobs = {}, [], {}, {}
    for base, ln, m, _ in a.parts:
        pos = m.find(b'xff2')
        while pos != -1:
            if pos + 0x70 <= ln:
                w = struct.unpack_from('<28I', m, pos)
                ok = (w[1] == w[2] == w[4] == w[6] == 0 and w[26] == 0x70 and
                      w[17] == 0x40010070 and w[10] == w[21] + 0x40010000 and
                      0x70 < w[5] < 0x4000000 and w[21] < w[5])
                if ok:
                    g = base + pos
                    data = a.read(g, w[5])
                    key = hashlib.sha1(data).hexdigest()[:16]
                    if key in seen:
                        seen[key]['copies'].append(g)
                    else:
                        try:
                            defs, undef = xff_globals(data); err = None
                        except Exception as e:  # noqa: BLE001
                            defs, undef, err = [], [], str(e)
                        name = f'xff_{key}.xff'
                        ent = {'file': name, 'offset': g, 'copies': [g], 'size': w[5],
                               'defines': len(defs), 'imports': len(undef), 'error': err,
                               'sample_defs': defs[:5]}
                        seen[key] = ent; index.append(ent); blobs[name] = data
                        for sname in defs:
                            sym_owner.setdefault(sname, []).append(name)
            pos = m.find(b'xff2', pos + 4)
    result = {'containers': index, 'symbol_owner': sym_owner}
    if ext_file:
        externs = json.load(open(ext_file))['externs']
        hit = {s: sym_owner[s] for s in externs if s in sym_owner}
        miss = sorted(s for s in externs if s not in sym_owner)
        result['extern_resolution'] = {'resolved': hit, 'unresolved': miss}
        need = sorted({v[0] for v in hit.values()})
        result['needed_containers'] = need
        for name in need:
            with open(os.path.join(out, name), 'wb') as f:
                f.write(blobs[name])
        print(f'externs resolved {len(hit)}/{len(externs)} by {len(need)} containers; '
              f'unresolved sample: {miss[:15]}')
    with open(os.path.join(out, 'index.json'), 'w') as f:
        json.dump(result, f, indent=1)
    print(f'{len(index)} unique xff2 containers ({sum(len(e["copies"]) for e in index)} copies), archive size {a.size:#x}')


if __name__ == '__main__':
    main()

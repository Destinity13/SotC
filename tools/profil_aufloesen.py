#!/usr/bin/env python3
"""profil_roh.txt (SOTC_PROFILE) aufloesen: Binary per nm, Bibliotheken ueber die mitgeschriebenen '# map'-Zeilen.
  python3 -I tools/profil_aufloesen.py <profil_roh.txt> <binary> [top]"""
import bisect, os, subprocess, sys
from collections import Counter, defaultdict

M44 = (1 << 44) - 1  # Rohdaten speichern den Versatz nur mit 44 Bit (Schluessel rip<<20)
roh, binp = sys.argv[1], sys.argv[2]
top = int(sys.argv[3]) if len(sys.argv) > 3 else 40

def load_syms(path, dynamic=False):
    out = []
    for args in ([['nm', '-C', '-S', '--defined-only', path]] + ([['nm', '-C', '-S', '-D', '--defined-only', path]] if dynamic else [])):
        try:
            txt = subprocess.run(args, capture_output=True, text=True).stdout
        except OSError:
            continue
        for line in txt.splitlines():
            p = line.split(' ', 3)
            if len(p) == 4 and p[2] in 'tTwWi':
                out.append((int(p[0], 16), int(p[1], 16), p[3]))
            elif len(p) == 3 and p[1] in 'tTwW':
                out.append((int(p[0], 16), 0, p[2]))
    out.sort()
    return out

class Table:
    def __init__(self, syms):
        self.syms = syms
        self.addrs = [a for a, _, _ in syms]
    def name(self, a):
        k = bisect.bisect_right(self.addrs, a) - 1
        if k < 0:
            return None
        s, size, n = self.syms[k]
        if size and a >= s + size + 0x40:
            return None
        return n

binsyms = load_syms(binp)
bintab = Table(binsyms)
bin_end = max((a + s for a, s, _ in binsyms), default=0)
maps = []  # (lo, hi, off, path)
samples = []
for line in open(roh):
    if line.startswith('# map '):
        _, _, lo, hi, off, path = line.rstrip('\n').split(' ', 5)
        lo_, hi_ = int(lo, 16) & M44, int(hi, 16) & M44
        maps.append((lo_, hi_ if hi_ > lo_ else lo_ + ((int(hi, 16) - int(lo, 16)) & M44), int(off, 16), path))
        continue
    if line.startswith('#'):
        continue
    a, t, c = line.split()
    samples.append((int(a, 16), t, int(c)))

libtabs = {}
def resolve(a):
    if a <= bin_end + 0x1000:
        return bintab.name(a) or '[Binary ohne Symbol]'
    for lo, hi, off, path in maps:
        if lo <= a < hi:
            base = os.path.basename(path)
            if path.startswith('/') and os.path.exists(path):
                if path not in libtabs:
                    libtabs[path] = Table(load_syms(path, dynamic=True))
                n = libtabs[path].name(a - lo + off)
                return '%s: %s' % (base, n or '?')
            return base
    return '[unbekannte Adresse]'

fn = Counter(); lib = Counter(); thr = Counter(); thrfn = defaultdict(Counter); tot = 0
for a, t, c in samples:
    name = resolve(a)
    fn[name] += c; thr[t] += c; thrfn[t][name] += c; tot += c
    lib[name.split(':')[0] if ':' in name else 'Binary'] += c
print(f'Proben gesamt: {tot}')
print('--- Threads (Anteil):')
for t, c in thr.most_common(8):
    print(f'  tid {t}: {100*c/tot:5.1f}%   top: ' + '; '.join(f'{n[:60]} {100*x/c:.0f}%' for n, x in thrfn[t].most_common(3)))
print('--- Module:')
for n, c in lib.most_common(10):
    print(f'  {100*c/tot:5.1f}%  {n}')
for t, c in thr.most_common(2):
    print(f'--- Thread {t} Top 25:')
    for n, x in thrfn[t].most_common(25):
        print(f'  {100*x/c:5.1f}%  {n[:140]}')
print(f'--- Top {top} Funktionen:')
for n, c in fn.most_common(top):
    print(f'  {100*c/tot:5.1f}%  {n[:150]}')

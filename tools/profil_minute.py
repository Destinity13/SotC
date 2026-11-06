#!/usr/bin/env python3
"""profil_minute.py <spiel_profil/ordner> <von_minute> [<bis_minute>] [top]

Spielprofil einer Zeitspanne: Differenz der kumulativen Zwischenstaende profil_mNNN.txt (SOTC_PROFILE_MINUTEN=1),
aufgeloest mit tools/profil_aufloesen.py gegen die Binary aus binary.txt."""
import os, subprocess, sys, tempfile

def lade(p):
    kopf, d = [], {}
    for l in open(p):
        if l.startswith('#'): kopf.append(l); continue
        a, t, c = l.split(); d[(a, t)] = int(c)
    return kopf, d

def main():
    ordner, von = sys.argv[1], int(sys.argv[2])
    bis = int(sys.argv[3]) if len(sys.argv) > 3 else von + 1
    top = sys.argv[4] if len(sys.argv) > 4 else '30'
    k1, a = lade(os.path.join(ordner, 'profil_m%03u.txt' % von)) if von > 0 else ([], {})
    k2, b = lade(os.path.join(ordner, 'profil_m%03u.txt' % bis))
    diff = {k: v - a.get(k, 0) for k, v in b.items() if v - a.get(k, 0) > 0}
    binp = open(os.path.join(ordner, 'binary.txt')).read().strip()
    # Symbol-Binary: die PGO-Ausgabe hat Symbole; die kopierte Spieler-Binary ist identisch gebaut
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        f.writelines(l.replace('proben', 'proben') for l in k2)
        for (adr, tid), c in diff.items(): f.write('%s %s %d\n' % (adr, tid, c))
    print('Minuten %d-%d: %d Proben' % (von, bis, sum(diff.values())))
    subprocess.run([sys.executable, '-I', os.path.join(os.path.dirname(__file__), 'profil_aufloesen.py'), f.name, binp, top])
    os.unlink(f.name)

if __name__ == '__main__':
    main()

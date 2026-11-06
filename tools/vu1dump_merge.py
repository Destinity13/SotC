#!/usr/bin/env python3
"""vu1dump_merge.py <ziel> <mitschnitt1> [<mitschnitt2> ...]

Fuehrt VU1-Mitschnitte (PS2X_VU1_DUMP) zusammen, damit der Uebersetzer (tools/vu1rec.py) alle jemals
ausgefuehrten Befehlspaare kennt:
  vu1_<hash>.bin / .dec : aus dem ersten Mitschnitt, der sie hat (Inhalt ist je Hash identisch)
  vu1_<hash>.cov        : Vereinigung aller ausgefuehrten Paare
  vu1_index.txt         : alle Zeilen aller Mitschnitte (vu1rec summiert je Hash)
"""
import os, shutil, sys

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    out, srcs = sys.argv[1], sys.argv[2:]
    os.makedirs(out, exist_ok=True)
    cov = {}
    have = set()
    idx = []
    for d in srcs:
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f == "vu1_index.txt":
                idx += [l for l in open(p) if l.strip()]
            elif f.endswith(".cov"):
                h = f[4:-4]
                cov.setdefault(h, set()).update(x for x in open(p).read().split() if x)
            elif (f.endswith(".bin") or f.endswith(".dec") or f.endswith(".len")) and f not in have:
                shutil.copyfile(p, os.path.join(out, f))
                have.add(f)
    for h, pcs in cov.items():
        with open(os.path.join(out, "vu1_%s.cov" % h), "w") as fo:
            fo.write("\n".join(sorted(pcs, key=lambda x: int(x, 16))) + "\n")
    with open(os.path.join(out, "vu1_index.txt"), "w") as fo:
        fo.writelines(idx)
    print("%d Programme, %d Indexzeilen -> %s" % (len(cov), len(idx), out))
    for h in sorted(cov):
        print("  %s: %d Paare abgedeckt" % (h, len(cov[h])))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""VU1-Mikrocode-Disassembler/Analyse fuer die SotC-Mitschnitte (PS2X_VU1_DUMP).

Aufruf:  vu1dis.py <ordner> [hash]      -> Statistik bzw. Listing eines Programms
Dekodierung nach den Tabellen unseres Interpreters (ps2_vu1_upper.cpp / ps2_vu1_lower.cpp).
"""
import os, struct, sys, collections

BC = "xyzw"

def dest(i):
    d = (i >> 21) & 0xF
    return "".join(c for b, c in zip((8, 4, 2, 1), "xyzw") if d & b) or "-"

def upper_name(u):
    op = u & 0x3F
    if op < 0x3C:
        t = {0: "ADD", 1: "SUB", 2: "MADD", 3: "MSUB", 4: "MAX", 5: "MINI", 6: "MUL"}
        if op < 0x1C:
            return t[op >> 2] + BC[op & 3]
        return {0x1C: "MULq", 0x1D: "MAXi", 0x1E: "MULi", 0x1F: "MINIi", 0x20: "ADDq", 0x21: "MADDq", 0x22: "ADDi",
                0x23: "MADDi", 0x24: "SUBq", 0x25: "MSUBq", 0x26: "SUBi", 0x27: "MSUBi", 0x28: "ADD", 0x29: "MADD",
                0x2A: "MUL", 0x2B: "MAX", 0x2C: "SUB", 0x2D: "MSUB", 0x2E: "OPMSUB", 0x2F: "MINI"}.get(op, "U?%02x" % op)
    sp = (u & 3) | ((u >> 4) & 0x7C)
    if sp < 0x10:
        return ["ADDA", "SUBA", "MADDA", "MSUBA"][sp >> 2] + BC[sp & 3]
    return {0x10: "ITOF0", 0x11: "ITOF4", 0x12: "ITOF12", 0x13: "ITOF15", 0x14: "FTOI0", 0x15: "FTOI4", 0x16: "FTOI12",
            0x17: "FTOI15", 0x18: "MULAx", 0x19: "MULAy", 0x1A: "MULAz", 0x1B: "MULAw", 0x1C: "MULAq", 0x1D: "ABS",
            0x1E: "MULAi", 0x1F: "CLIP", 0x20: "ADDAq", 0x21: "MADDAq", 0x22: "ADDAi", 0x23: "MADDAi", 0x24: "SUBAq",
            0x25: "MSUBAq", 0x26: "SUBAi", 0x27: "MSUBAi", 0x28: "ADDA", 0x29: "MADDA", 0x2A: "MULA", 0x2C: "SUBA",
            0x2D: "MSUBA", 0x2E: "OPMULA", 0x2F: "NOP", 0x30: "NOP"}.get(sp, "US?%02x" % sp)

LOWER = {0x00: "LQ", 0x01: "SQ", 0x04: "ILW", 0x05: "ISW", 0x08: "IADDIU", 0x09: "ISUBIU", 0x10: "FCEQ", 0x11: "FCSET",
         0x12: "FCAND", 0x13: "FCOR", 0x14: "FSEQ", 0x15: "FSSET", 0x16: "FSAND", 0x17: "FSOR", 0x18: "FMEQ",
         0x1A: "FMAND", 0x1B: "FMOR", 0x1C: "FCGET", 0x20: "B", 0x21: "BAL", 0x24: "JR", 0x25: "JALR", 0x28: "IBEQ",
         0x29: "IBNE", 0x2C: "IBLTZ", 0x2D: "IBGTZ", 0x2E: "IBLEZ", 0x2F: "IBGEZ"}
LOWER1 = {0x30: "IADD", 0x31: "ISUB", 0x32: "IADDI", 0x34: "IAND", 0x35: "IOR"}
LOWERS = {0x30: "MOVE", 0x31: "MR32", 0x34: "LQI", 0x35: "SQI", 0x36: "LQD", 0x37: "SQD", 0x38: "DIV", 0x39: "SQRT",
          0x3A: "RSQRT", 0x3B: "WAITQ", 0x3C: "MTIR", 0x3D: "MFIR", 0x3E: "ILWR", 0x3F: "ISWR", 0x40: "RNEXT",
          0x41: "RGET", 0x42: "RINIT", 0x43: "RXOR", 0x64: "MFP", 0x68: "XTOP", 0x69: "XITOP", 0x6C: "XGKICK",
          0x70: "ESADD", 0x71: "ERSADD", 0x72: "ELENG", 0x73: "ERLENG", 0x74: "EATANxy", 0x75: "EATANxz", 0x76: "ESUM",
          0x77: "ERSQRT", 0x78: "ESQRT", 0x79: "ESIN", 0x7A: "ERCPR", 0x7B: "WAITP", 0x7C: "EATAN", 0x7D: "EEXP"}

def lower_name(l):
    if l >> 31:
        op = l & 0x3F
        if op < 0x3C:
            return LOWER1.get(op, "L1?%02x" % op)
        sp = (l & 3) | ((l >> 4) & 0x7C)
        return LOWERS.get(sp, "LS?%02x" % sp)
    return LOWER.get(l >> 25, "L?%02x" % (l >> 25))

BRANCHES = {"B", "BAL", "IBEQ", "IBNE", "IBLTZ", "IBGTZ", "IBLEZ", "IBGEZ"}
JUMPS = {"JR", "JALR"}

def imm11(l):
    v = l & 0x7FF
    return v - 0x800 if v & 0x400 else v

def reachable(code, entries):
    """Folgt Spruengen ab den Einsprungpunkten (Byte-PCs). JR/JALR-Ziele unbekannt -> Ende."""
    seen, todo = set(), list(entries)
    n = len(code) // 8
    while todo:
        pc = todo.pop()
        while 0 <= pc < len(code) and pc not in seen:
            seen.add(pc)
            l, u = struct.unpack_from("<II", code, pc)
            ln = lower_name(l) if not (u >> 31) else "I"  # I-Bit: lower ist Konstante
            nxt = pc + 8
            if (u >> 30) & 1:  # E-Bit: Delay-Slot noch, dann Ende
                seen.add(nxt)
                break
            if ln in BRANCHES:
                tgt = nxt + imm11(l) * 8
                todo.append(tgt & 0x3FFF)
                seen.add(nxt)  # Delay-Slot
                if ln == "B":
                    break
                pc = nxt + 8
                continue
            if ln in JUMPS:
                seen.add(nxt)
                break
            pc = nxt
    return seen

def main():
    d = sys.argv[1]
    idx = collections.defaultdict(list)
    for line in open(os.path.join(d, "vu1_index.txt")):
        h, pc, calls, cyc = line.split()
        idx[h].append((int(pc, 16), int(calls), int(cyc)))
    if len(sys.argv) > 2:
        h = sys.argv[2]
        code = open(os.path.join(d, "vu1_%s.bin" % h), "rb").read()
        r = reachable(code, [pc for pc, _, _ in idx[h]])
        for pc in sorted(r):
            l, u = struct.unpack_from("<II", code, pc)
            bits = "".join(c for b, c in ((31, "I"), (30, "E"), (29, "M"), (28, "D"), (27, "T")) if (u >> b) & 1)
            ln = ("const %08x" % l) if (u >> 31) else lower_name(l)
            print("%04x  %-8s %-5s | %-8s %-5s %s" % (pc, upper_name(u), dest(u), ln, dest(l) if l >> 31 else "", bits))
        return
    tot_cyc = sum(c for v in idx.values() for _, _, c in v)
    hist = collections.Counter()
    weighted = collections.Counter()
    print("Programme: %d, Zyklen gesamt %d" % (len(idx), tot_cyc))
    for h, ents in sorted(idx.items(), key=lambda kv: -sum(c for _, _, c in kv[1])):
        code = open(os.path.join(d, "vu1_%s.bin" % h), "rb").read()
        r = reachable(code, [pc for pc, _, _ in ents])
        cyc = sum(c for _, _, c in ents)
        names = collections.Counter()
        for pc in r:
            l, u = struct.unpack_from("<II", code, pc)
            names[upper_name(u)] += 1
            names["const" if u >> 31 else lower_name(l)] += 1
        hist.update(names)
        for k, v in names.items():
            weighted[k] += v * cyc / max(1, len(r))
        flags = sorted(k for k in names if k[:2] in ("FM", "FS", "FC") or k == "CLIP")
        print("%s  %5.1f%% Zyklen  %4d Paare  Einspruenge %s  Flags: %s" % (
            h, 100.0 * cyc / tot_cyc, len(r), ",".join("%x" % p for p, _, _ in sorted(ents)), " ".join(flags)))
    print("\nBefehle (zyklengewichtet):")
    for k, v in weighted.most_common(60):
        print("  %-9s %7.2f%%" % (k, 100.0 * v / sum(weighted.values())))

if __name__ == "__main__":
    main()

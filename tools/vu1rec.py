#!/usr/bin/env python3
"""VU1-Rekompilierer fuer SotC: erzeugt src/lib/vu/ps2_vu1_compiled.cpp aus den Mitschnitten (PS2X_VU1_DUMP).

Stufe 1 ("exakt"): jedes ausgefuehrte Befehlspaar wird als eigener C++-Block erzeugt, der genau dasselbe tut wie ein
Durchlauf von VU1Interpreter::run(): Dekodierung, Lese-/Schreiblisten, Latenzen und Wartezyklen sind aber schon zur
Uebersetzungszeit aufgeloest (Konstanten statt Tabellen/Schleifen). Ergebnis ist bitgleich zum Interpreter; geprueft
wird das mit PS2X_VU1_DIFF=1.

Aufruf:  vu1rec.py <dump-ordner> <ausgabe.cpp> [min_anteil_prozent] [stufe 1|2]
"""
import re
import os, struct, sys, collections

PIPE_FDIV, PIPE_EFU, PIPE_BRANCH, PIPE_XGKICK = 3, 4, 6, 7
LANE = {0: 8, 1: 4, 2: 2, 3: 1}  # laneForComponent

class Usage:
    FIELDS = ("r0reg r0lanes r1reg r1lanes nread wreg wlanes viRead viWrite accRead accWrite lat vfLat viLat pipe "
              "waitQ waitP rClip wClip delayBr reserved").split()
    def __init__(self, vals):
        for k, v in zip(self.FIELDS, vals):
            setattr(self, k, int(v))
    def vfreads(self):
        out = []
        for idx in range(self.nread):
            reg, lanes = (self.r0reg, self.r0lanes) if idx == 0 else (self.r1reg, self.r1lanes)
            out.append((reg, lanes))
        return out

class Pair:
    def __init__(self, line):
        p = line.split()
        self.pc = int(p[0], 16)
        self.lower = int(p[1], 16)
        self.upper = int(p[2], 16)
        self.iBit, self.eBit, self.mBit, self.dBit, self.tBit, self.shadow, self.suppressed = (int(x) for x in p[3:10])
        self.uu = Usage(p[10:31])
        self.lu = Usage(p[31:52])


STUFE = 2  # per Kommandozeile umschaltbar
FLAG_TARGET = "pipe"
COVERED = set()
STUFE3_OK = set()
HEADS = set()
NEED_STICKY = True
SH = {}        # Stufe 6: Register mit gueltigem normalisierten Schatten sh[reg]
SHA = [False]  # Stufe 6: ACC-Schatten gueltig
LAST_FMAC = [None]  # (fd oder 'acc') des zuletzt ausgegebenen fmac6, sonst None
FALLBACK = [False]  # Stufe 6: im aktuellen Paar wurde auf execUpper/execLower zurueckgefallen

def lanes_of(dest):
    return [c for c in range(4) if dest & LANE[c]]

def upper_inline(U):
    """Upper-Befehl direkt als C++ (nur haeufige FMAC-Befehle). None -> execUpper aufrufen."""
    dest, ft, fs, fd, op = (U >> 21) & 0xF, (U >> 16) & 0x1F, (U >> 11) & 0x1F, (U >> 6) & 0x1F, U & 0x3F
    N = "vu1NormalizeOperandInline"
    vs = lambda c: "%s(v.m_state.vf[%d][%d])" % (N, fs, c)
    vt = lambda c: "%s(v.m_state.vf[%d][%d])" % (N, ft, c)
    ac = lambda c: "%s(v.m_state.acc[%d])" % (N, c)
    pre = ["v.m_currentUpperInstruction = 0x%08xu;" % U]
    if op >= 0x3C:
        sp = (U & 3) | ((U >> 4) & 0x7C)
        if sp in (0x2F, 0x30):
            return pre  # NOP
        toacc = True
        if sp < 0x10:
            kind, src = ["ADD", "SUB", "MADD", "MSUB"][sp >> 2], ("bc", sp & 3)
        elif 0x18 <= sp <= 0x1B:
            kind, src = "MUL", ("bc", sp & 3)
        elif sp in (0x1C, 0x1E):
            kind, src = "MUL", ("q" if sp == 0x1C else "i", None)
        elif 0x20 <= sp <= 0x27:
            kind = ["ADD", "MADD", "ADD", "MADD", "SUB", "MSUB", "SUB", "MSUB"][sp - 0x20]
            src = ("q" if (sp & 2) == 0 else "i", None)
        elif sp in (0x28, 0x29, 0x2A, 0x2C, 0x2D):
            kind, src = {0x28: "ADD", 0x29: "MADD", 0x2A: "MUL", 0x2C: "SUB", 0x2D: "MSUB"}[sp], ("vt", None)
        elif sp == 0x2E:
            kind, src = "OPMULA", ("vt", None)
        else:
            return None
    else:
        toacc = False
        if op < 0x10 or 0x18 <= op <= 0x1B:
            kind, src = ({0: "ADD", 1: "SUB", 2: "MADD", 3: "MSUB", 6: "MUL"}[op >> 2]), ("bc", op & 3)
        elif op in (0x1C, 0x1E):
            kind, src = "MUL", ("q" if op == 0x1C else "i", None)
        elif 0x20 <= op <= 0x27:
            kind = ["ADD", "MADD", "ADD", "MADD", "SUB", "MSUB", "SUB", "MSUB"][op - 0x20]
            src = ("q" if (op & 2) == 0 else "i", None)
        elif op in (0x28, 0x29, 0x2A, 0x2C, 0x2D):
            kind, src = {0x28: "ADD", 0x29: "MADD", 0x2A: "MUL", 0x2C: "SUB", 0x2D: "MSUB"}[op], ("vt", None)
        elif op == 0x2E:
            kind, src = "OPMSUB", ("vt", None)
        else:
            return None
    body = ["float res[4] = {0.0f, 0.0f, 0.0f, 0.0f};"]
    if src[0] == "bc":
        body.append("const float bcv = %s;" % vt(src[1]))
        t = lambda c: "bcv"
    elif src[0] == "q":
        body.append("const float qv = %s(v.m_state.q);" % N)
        t = lambda c: "qv"
    elif src[0] == "i":
        body.append("const float iv = %s(v.m_state.i);" % N)
        t = lambda c: "iv"
    else:
        t = vt
    OP3 = {0: (1, 2), 1: (2, 0), 2: (0, 1)}  # OPMULA/OPMSUB: x = s.y*t.z, y = s.z*t.x, z = s.x*t.y, w = 0
    for c in lanes_of(dest):
        if kind in ("OPMULA", "OPMSUB"):
            if c == 3:
                body.append("res[3] = 0.0f;")
                continue
            a_, b_ = OP3[c]
            prod = "%s * %s" % (vs(a_), vt(b_))
            body.append("res[%d] = %s;" % (c, prod if kind == "OPMULA" else "%s - %s" % (ac(c), prod)))
            continue
        e = {"ADD": "%s + %s" % (vs(c), t(c)), "SUB": "%s - %s" % (vs(c), t(c)), "MUL": "%s * %s" % (vs(c), t(c)),
             "MADD": "%s + %s * %s" % (ac(c), vs(c), t(c)), "MSUB": "%s - %s * %s" % (ac(c), vs(c), t(c))}[kind]
        body.append("res[%d] = %s;" % (c, e))
    # FAST (Standard): Normalisieren + Flags direkt, ohne Schleifen/Aufrufe. Sonst die exakten Helfer.
    lanes = lanes_of(dest)
    fast = ["if (vu1Fast()) {", "uint32_t mac = 0u, st = 0u;"]
    for c in lanes:
        lane = LANE[c]
        fast.append("{ uint32_t b; std::memcpy(&b, &res[%d], 4); uint32_t f = (b & 0x80000000u) ? 2u : 0u;" % c)
        fast.append("  const uint32_t e = (b >> 23) & 0xFFu; if ((b & 0x7FFFFFFFu) == 0u) f |= 1u; else if (e == 0u) { f |= 5u; b &= 0x80000000u; } else if (e == 0xFFu) { f |= 8u; b = (b & 0x80000000u) | 0x7F7FFFFFu; }")
        fast.append("  std::memcpy(&res[%d], &b, 4); st |= f; mac |= ((f & 1u) ? %du : 0u) | ((f & 2u) ? %du : 0u) | ((f & 4u) ? %du : 0u) | ((f & 8u) ? %du : 0u); }" % (c, lane, lane << 4, lane << 8, lane << 12))
    if lanes:
        if FLAG_TARGET == "ring":
            fast.append("if (v.m_flagsLive) fr.push(v, v.m_cycle, mac, st); else { (void)mac; (void)st; }")
        else:
            fast.append("if (v.m_flagsLive) v.pushFmacFlags(mac, st); else { (void)mac; (void)st; }")
    for c in lanes:
        fast.append(("v.m_state.acc[%d] = res[%d];" % (c, c)) if toacc else ("v.m_state.vf[%d][%d] = res[%d];" % (fd, c, c)))
    fast.append("} else {")
    fast.append(("v.applyFmacDestAcc(res, %d);" % dest) if toacc else ("v.applyFmacDest(v.m_state.vf[%d], res, %d);" % (fd, dest)))
    fast.append("}")
    body += fast
    return pre + ["{ " + " ".join(body) + " }"]

def lower_inline(L, pc):
    """Lower-Befehl direkt als C++ (haeufige). None -> execLower aufrufen."""
    if L in (0x00000000, 0x8000033C):
        return []
    opHi = (L >> 25) & 0x7F
    dest = (L >> 21) & 0xF
    ft, fs = (L >> 16) & 0x1F, (L >> 11) & 0x1F
    vit, vis = (L >> 16) & 0xF, (L >> 11) & 0xF
    imm11 = L & 0x7FF
    if imm11 & 0x400:
        imm11 -= 0x800
    if opHi == 0x00:  # LQ
        lines = ["{ uint32_t addr = ((uint32_t)(int32_t)(v.m_state.vi[%d] + (%d))) * 16u; addr &= (dataSize - 1);" % (vis, imm11),
                 "  if (addr + 16 <= dataSize) { float tmp[4]; std::memcpy(tmp, vuData + addr, 16);"]
        lines += ["    v.m_state.vf[%d][%d] = tmp[%d];" % (ft, c, c) for c in lanes_of(dest)]
        lines.append("  } }")
        return lines
    # Speichern sofort statt ueber die 1-Takt-Store-Pipeline: der Interpreter macht den Wert im advanceOneCycle()
    # desselben Paares sichtbar, noch bevor XGKICK oder das naechste Paar lesen -> gleiches Ergebnis.
    # m_recPend haelt das Programmende-Warten (flush) gleich.
    def store(addrexpr, words_c):
        lines = ["{ uint32_t addr = %s; addr &= (dataSize - 1);" % addrexpr, "  if (addr + 16 <= dataSize) {"]
        lines += ["    std::memcpy(vuData + addr + %d, %s, 4);" % (4 * c, words_c(c)) for c in lanes_of(dest)]
        lines += ["  }", "  if (v.m_cycle + 1u > v.m_recPend) v.m_recPend = v.m_cycle + 1u; }"]
        return lines
    if opHi == 0x01:  # SQ
        return store("((uint32_t)(int32_t)(v.m_state.vi[%d] + (%d))) * 16u" % (vit, imm11), lambda c: "&v.m_state.vf[%d][%d]" % (fs, c))
    if opHi == 0x05:  # ISW
        lines = ["{ const uint32_t isw = static_cast<uint32_t>(static_cast<uint16_t>(v.m_state.vi[%d] & 0xFFFF));" % vit]
        lines += store("((uint32_t)(int32_t)(v.m_state.vi[%d] + (%d))) * 16u" % (vis, imm11), lambda c: "&isw")
        return lines + ["}"]
    if opHi == 0x04:  # ILW
        comp = 0 if dest & 8 else 1 if dest & 4 else 2 if dest & 2 else 3
        if vit == 0:
            return []
        return ["{ uint32_t addr = ((uint32_t)(int32_t)(v.m_state.vi[%d] + (%d))) * 16u; addr &= (dataSize - 1);" % (vis, imm11),
                "  if (addr + 16 <= dataSize) { uint32_t w; std::memcpy(&w, vuData + addr + %d, 4); v.m_state.vi[%d] = (int32_t)(int16_t)(w & 0xFFFF); } }" % (comp * 4, vit)]
    if opHi in (0x08, 0x09):  # IADDIU / ISUBIU
        imm = (L & 0x7FF) | ((L >> 10) & 0x7800)
        imm = imm - 0x10000 if imm & 0x8000 else imm
        if vit == 0:
            return []
        return ["v.m_state.vi[%d] = (int16_t)(v.m_state.vi[%d] %s (%d));" % (vit, vis, "+" if opHi == 0x08 else "-", imm)]
    # Flag-/Clip-Leser (bitgleich zu execLower; der Flag-Ring ist vorher eingetragen, siehe lower3_inline)
    imm24 = L & 0xFFFFFF
    imm12 = (((L >> 21) & 1) << 11) | (L & 0x7FF)
    if opHi == 0x12:  # FCAND
        return ["v.m_state.vi[1] = ((v.m_state.clip & 0x%06xu) != 0u) ? 1 : 0;" % imm24]
    if opHi == 0x13:  # FCOR
        return ["v.m_state.vi[1] = ((v.m_state.clip | 0x%06xu) == 0xFFFFFFu) ? 1 : 0;" % imm24]
    if opHi == 0x10:  # FCEQ
        return ["v.m_state.vi[1] = ((v.m_state.clip & 0xFFFFFFu) == 0x%06xu) ? 1 : 0;" % imm24]
    if opHi == 0x1C:  # FCGET
        return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)(v.m_state.clip & 0x0FFFu);" % vit]
    if opHi == 0x14:  # FSEQ
        return [] if vit == 0 else ["v.m_state.vi[%d] = ((v.m_state.status & 0xFFFu) == 0x%03xu) ? 1 : 0;" % (vit, imm12)]
    if opHi == 0x16:  # FSAND
        return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)((v.m_state.status & 0xFFFu) & 0x%03xu);" % (vit, imm12)]
    if opHi == 0x17:  # FSOR
        return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)((v.m_state.status & 0xFFFu) | 0x%03xu);" % (vit, imm12)]
    if opHi == 0x18:  # FMEQ
        return [] if vit == 0 else ["v.m_state.vi[%d] = ((v.m_state.mac & 0xFFFFu) == (uint32_t)(uint16_t)v.m_state.vi[%d]) ? 1 : 0;" % (vit, vis)]
    if opHi == 0x1A:  # FMAND
        return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)(v.m_state.mac & (uint32_t)(uint16_t)v.m_state.vi[%d]);" % (vit, vis)]
    if opHi == 0x1B:  # FMOR
        return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)(v.m_state.mac | (uint32_t)(uint16_t)v.m_state.vi[%d]);" % (vit, vis)]
    target = (pc + 8 + imm11 * 8) & 0x3FFF
    take = "{ v.m_state.branchPending = true; v.m_state.branchTarget = 0x%04xu; v.m_state.branchDelay = 1; }" % target
    if opHi == 0x20:  # B
        return [take]
    if opHi in (0x28, 0x29):  # IBEQ / IBNE
        return ["if ((int16_t)v.readBranchVi(%d) %s (int16_t)v.readBranchVi(%d)) %s" % (vis, "==" if opHi == 0x28 else "!=", vit, take)]
    if opHi == 0x40 and (L & 0x3F) < 0x3C:
        funct = L & 0x3F
        vid = (L >> 6) & 0xF
        if funct in (0x30, 0x31, 0x34, 0x35):  # IADD ISUB IAND IOR
            if vid == 0:
                return []
            if funct in (0x30, 0x31):
                return ["v.m_state.vi[%d] = (int16_t)(v.m_state.vi[%d] %s v.m_state.vi[%d]);" % (vid, vis, "+" if funct == 0x30 else "-", vit)]
            return ["v.m_state.vi[%d] = v.m_state.vi[%d] %s v.m_state.vi[%d];" % (vid, vis, "&" if funct == 0x34 else "|", vit)]
        if funct == 0x32:  # IADDI
            imm5 = (L >> 6) & 0x1F
            imm5 = imm5 - 32 if imm5 & 0x10 else imm5
            if vit == 0:
                return []
            return ["v.m_state.vi[%d] = (int16_t)(v.m_state.vi[%d] + (%d));" % (vit, vis, imm5)]
        return None
    if opHi == 0x40 and (L & 0x3F) >= 0x3C:
        sp = (L & 3) | ((L >> 4) & 0x7C)
        if sp == 0x30:  # MOVE
            return ["{ float tmp[4]; std::memcpy(tmp, v.m_state.vf[%d], 16);" % fs] + \
                   ["  v.m_state.vf[%d][%d] = tmp[%d];" % (ft, c, c) for c in lanes_of(dest)] + ["}"]
        if sp == 0x34:  # LQI
            lines = ["{ uint32_t addr = ((uint32_t)(uint16_t)v.m_state.vi[%d]) * 16u; addr &= (dataSize - 1);" % vis,
                     "  if (addr + 16 <= dataSize) { float tmp[4]; std::memcpy(tmp, vuData + addr, 16);"]
            lines += ["    v.m_state.vf[%d][%d] = tmp[%d];" % (ft, c, c) for c in lanes_of(dest)]
            lines.append("  }")
            if vis != 0:
                lines.append("  v.m_state.vi[%d] = (int16_t)(v.m_state.vi[%d] + 1);" % (vis, vis))
            return lines + ["}"]
        if sp == 0x35:  # SQI
            lines = store("((uint32_t)(uint16_t)v.m_state.vi[%d]) * 16u" % vit, lambda c: "&v.m_state.vf[%d][%d]" % (fs, c))
            if vit != 0:
                lines.append("v.m_state.vi[%d] = (int16_t)(v.m_state.vi[%d] + 1);" % (vit, vit))
            return lines
        fsf, ftf = (L >> 21) & 3, (L >> 23) & 3
        if sp == 0x38:  # DIV (Q-Pipeline, Latenz 7)
            return ["{ const float num = vu1NormalizeOperandInline(v.m_state.vf[%d][%d]); const float den = vu1NormalizeOperandInline(v.m_state.vf[%d][%d]);" % (fs, fsf, ft, ftf),
                    "  uint32_t di = 0u; float r;",
                    "  if (den == 0.0f) { di = num == 0.0f ? 0x10u : 0x20u; r = std::signbit(num) != std::signbit(den) ? -std::numeric_limits<float>::max() : std::numeric_limits<float>::max(); }",
                    "  else r = num / den;",
                    "  v.queueQ(r, 7u, di); }"]
        if sp == 0x3A:  # RSQRT (Q-Pipeline, Latenz 13)
            return ["{ const float num = vu1NormalizeOperandInline(v.m_state.vf[%d][%d]); const float rad = vu1NormalizeOperandInline(v.m_state.vf[%d][%d]);" % (fs, fsf, ft, ftf),
                    "  const float den = std::sqrt(std::fabs(rad)); uint32_t di = rad < 0.0f ? 0x10u : 0u; float r;",
                    "  if (den != 0.0f) r = num / den;",
                    "  else { di = num == 0.0f ? 0x10u : 0x20u; r = std::signbit(num) ? -std::numeric_limits<float>::max() : std::numeric_limits<float>::max(); }",
                    "  v.queueQ(r, 13u, di); }"]
        if sp == 0x39:  # SQRT (Q-Pipeline, Latenz 7)
            return ["{ const float val = vu1NormalizeOperandInline(v.m_state.vf[%d][%d]); v.queueQ(std::sqrt(std::fabs(val)), 7u, val < 0.0f ? 0x10u : 0u); }" % (ft, ftf)]
        if sp == 0x3B:  # WAITQ: das Warten steckt in der Bereitschaftsrechnung vor dem Paar
            return []
        if sp == 0x3C:  # MTIR
            return [] if vit == 0 else ["{ uint32_t f; std::memcpy(&f, &v.m_state.vf[%d][%d], 4); v.m_state.vi[%d] = (int32_t)(int16_t)(f & 0xFFFFu); }" % (fs, fsf, vit)]
        if sp == 0x3D:  # MFIR
            return ["{ const int32_t val = (int32_t)(int16_t)(v.m_state.vi[%d] & 0xFFFF); float fv; std::memcpy(&fv, &val, 4);" % vis] + \
                   ["  v.m_state.vf[%d][%d] = fv;" % (ft, c) for c in lanes_of(dest)] + ["}"]
        if sp == 0x3E:  # ILWR
            comp = 0 if dest & 8 else 1 if dest & 4 else 2 if dest & 2 else 3
            if vit == 0:
                return []
            return ["{ uint32_t addr = ((uint32_t)(uint16_t)v.m_state.vi[%d]) * 16u; addr &= (dataSize - 1);" % vis,
                    "  if (addr + 16 <= dataSize) { uint32_t wv; std::memcpy(&wv, vuData + addr + %d, 4); v.m_state.vi[%d] = (int32_t)(int16_t)(wv & 0xFFFFu); } }" % (comp * 4, vit)]
        if sp == 0x68:  # XTOP
            return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)(v.m_state.top & 0x3FFu);" % vit]
        if sp == 0x69:  # XITOP
            return [] if vit == 0 else ["v.m_state.vi[%d] = (int32_t)(v.m_state.itop & 0x3FFu);" % vit]
        if sp == 0x64:  # MFP
            return ["{ const float pv = v.m_state.p;"] + ["  v.m_state.vf[%d][%d] = pv;" % (ft, c) for c in lanes_of(dest)] + ["}"]
        if sp == 0x31:  # MR32
            return ["{ const float tmp[4] = {v.m_state.vf[%d][1], v.m_state.vf[%d][2], v.m_state.vf[%d][3], v.m_state.vf[%d][0]};" % (fs, fs, fs, fs)] + \
                   ["  v.m_state.vf[%d][%d] = tmp[%d];" % (ft, c, c) for c in lanes_of(dest)] + ["}"]
    return None

REV4 = [int('{:04b}'.format(i)[::-1], 2) for i in range(16)]

def upper_call5(U):
    """Stufe 5: FMAC als Aufruf eines gemeinsamen SSE-Helfers (wie microVU: wenig Code je Befehl -> passt in den
    Befehls-Cache). None -> bisherige Inline-Fassung."""
    dest, ft, fs, fd, op = (U >> 21) & 0xF, (U >> 16) & 0x1F, (U >> 11) & 0x1F, (U >> 6) & 0x1F, U & 0x3F
    K = {"ADD": 0, "SUB": 1, "MUL": 2, "MADD": 3, "MSUB": 4}
    if dest == 0:
        return None
    D = REV4[dest]
    if op >= 0x3C:
        sp = (U & 3) | ((U >> 4) & 0x7C)
        pre5 = "/* U %08x */" % U
        if 0x10 <= sp <= 0x13:  # ITOF0/4/12/15
            return [pre5, "itof5<%d>(v, %du, %du, %du);" % ([0, 4, 12, 15][sp - 0x10], ft, fs, D)]
        if 0x14 <= sp <= 0x17:  # FTOI0/4/12/15
            return [pre5, "ftoi5<%d>(v, %du, %du, %du);" % ([0, 4, 12, 15][sp - 0x14], ft, fs, D)]
        if sp == 0x1D:  # ABS
            return [pre5, "abs5(v, %du, %du, %du);" % (ft, fs, D)]
        if sp == 0x1F:  # CLIP (Flag-Pipeline des Interpreters)
            return [pre5, "clip5(v, cr, cyc, %du, %du);" % (fs, ft)]
        toacc = 1
        if sp < 0x10:
            kind, src, bc = ["ADD", "SUB", "MADD", "MSUB"][sp >> 2], 0, sp & 3
        elif 0x18 <= sp <= 0x1B:
            kind, src, bc = "MUL", 0, sp & 3
        elif sp in (0x1C, 0x1E):
            kind, src, bc = "MUL", (1 if sp == 0x1C else 2), 0
        elif 0x20 <= sp <= 0x27:
            kind = ["ADD", "MADD", "ADD", "MADD", "SUB", "MSUB", "SUB", "MSUB"][sp - 0x20]
            src, bc = (1 if (sp & 2) == 0 else 2), 0
        elif sp in (0x28, 0x29, 0x2A, 0x2C, 0x2D):
            kind, src, bc = {0x28: "ADD", 0x29: "MADD", 0x2A: "MUL", 0x2C: "SUB", 0x2D: "MSUB"}[sp], 3, 0
        else:
            return None
        fd = 0
    else:
        toacc = 0
        pre5 = "/* U %08x */" % U
        if 0x10 <= op <= 0x17:  # MAXbc / MINIbc
            return [pre5, "maxmin5<%d, 0>(v, %du, %du, %du, %du, %du);" % (1 if op < 0x14 else 0, fd, fs, ft, op & 3, D)]
        if op in (0x1D, 0x1F):  # MAXi / MINIi
            return [pre5, "maxmin5<%d, 2>(v, %du, %du, %du, 0u, %du);" % (1 if op == 0x1D else 0, fd, fs, ft, D)]
        if op in (0x2B, 0x2F):  # MAX / MINI
            return [pre5, "maxmin5<%d, 3>(v, %du, %du, %du, 0u, %du);" % (1 if op == 0x2B else 0, fd, fs, ft, D)]
        if op < 0x10 or 0x18 <= op <= 0x1B:
            kind, src, bc = ({0: "ADD", 1: "SUB", 2: "MADD", 3: "MSUB", 6: "MUL"}[op >> 2]), 0, op & 3
        elif op in (0x1C, 0x1E):
            kind, src, bc = "MUL", (1 if op == 0x1C else 2), 0
        elif 0x20 <= op <= 0x27:
            kind = ["ADD", "MADD", "ADD", "MADD", "SUB", "MSUB", "SUB", "MSUB"][op - 0x20]
            src, bc = (1 if (op & 2) == 0 else 2), 0
        elif op in (0x28, 0x29, 0x2A, 0x2C, 0x2D):
            kind, src, bc = {0x28: "ADD", 0x29: "MADD", 0x2A: "MUL", 0x2C: "SUB", 0x2D: "MSUB"}[op], 3, 0
        else:
            return None
    live = 0 if CUR_PC in DEAD_FLAGS else 1
    if STUFE >= 6:
        lines = ["/* U %08x */" % U]
        sv = sh_src(fs, lines)
        if src == 0:
            tv = ("_mm_set1_ps(%s)" % ("1.0f" if bc == 3 else "0.0f")) if ft == 0 else "vu6Bc<%d>(%s)" % (bc, sh_src(ft, lines))
        elif src == 1:
            tv = "_mm_set1_ps(vu1NormalizeOperandInline(v.m_state.q))"
        elif src == 2:
            tv = "_mm_set1_ps(vu1NormalizeOperandInline(v.m_state.i))"
        else:
            tv = sh_src(ft, lines)
        av = sh_acc(lines) if K[kind] >= 3 else sv
        if toacc:
            lines.append("sha = fmac6<%d, %d>(v, fr, %s, %s, %s, v.m_state.acc, %du);" % (K[kind], live, sv, tv, av, REV4[dest]))
            SHA[0] = True
            LAST_FMAC[0] = "acc"
        elif fd == 0:
            lines.append("(void)fmac6<%d, %d>(v, fr, %s, %s, %s, v.m_state.vf[0], %du);" % (K[kind], live, sv, tv, av, REV4[dest]))
            LAST_FMAC[0] = 0
        else:
            lines.append("sh[%d] = fmac6<%d, %d>(v, fr, %s, %s, %s, v.m_state.vf[%d], %du);" % (fd, K[kind], live, sv, tv, av, fd, REV4[dest]))
            SH[fd] = True
            LAST_FMAC[0] = fd
        return lines
    return ["/* U %08x */" % U,
            "fmac5<%d, %d, %d, %d, %d>(v, fr, cyc, %du, %du, %du, %du, %du);" % (K[kind], src, toacc, live, 1 if NEED_STICKY else 0, fd, fs, ft, bc, REV4[dest])]

def sh_src(reg, lines):
    """normalisierter Vektor eines VF-Registers (Stufe 6): Schatten anlegen, falls noch keiner gilt"""
    if reg == 0:
        return "kVu6Vf0"
    if reg not in SH:
        lines.append("sh[%d] = vu5Norm(_mm_loadu_ps(v.m_state.vf[%d]));" % (reg, reg))
        SH[reg] = True
    return "sh[%d]" % reg

def sh_acc(lines):
    if not SHA[0]:
        lines.append("sha = vu5Norm(_mm_loadu_ps(v.m_state.acc));")
        SHA[0] = True
    return "sha"


FMAC6 = r"""
// Stufe 6: schlanker FMAC. Voraussetzung: der Thread laeuft mit FTZ+DAZ (wie die VU: keine Denormalen), die
// Operanden sind bereits normalisiert ("Schatten": Exponent 0 -> +-0, 255 -> +-Max; ein Register wird nur beim
// ersten Lesen normalisiert und danach wiederverwendet, FMAC-Ergebnisse sind von sich aus sauber).
// Unterlauf wird von FTZ zu +-0 gespuelt (wie der Interpreter), Ueberlauf (+-Inf) auf +-Max geklemmt; NaN kann
// nicht entstehen, weil kein Operand unendlich ist. Flags nur, wenn jemand sie liest.
static const __m128 kVu6Vf0 = _mm_set_ps(1.0f, 0.0f, 0.0f, 0.0f);
template <int BC> static __attribute__((always_inline)) inline __m128 vu6Bc(__m128 x) { return _mm_shuffle_ps(x, x, _MM_SHUFFLE(BC, BC, BC, BC)); }
template <int K, int LIVE>
__m128 VU1Interpreter::Compiled::fmac6(VU1Interpreter &v, FlagRing &fr, __m128 s, __m128 t, __m128 a, float *dst, uint32_t dm)
{
    __m128 r, p = s;
    if constexpr (K == 0) r = _mm_add_ps(s, t);
    else if constexpr (K == 1) r = _mm_sub_ps(s, t);
    else if constexpr (K == 2) r = _mm_mul_ps(s, t);
    else { p = _mm_mul_ps(s, t); r = (K == 3) ? _mm_add_ps(a, p) : _mm_sub_ps(a, p); }
    const __m128 maxv = _mm_castsi128_ps(_mm_set1_epi32(0x7F7FFFFF));
    const __m128 absr = _mm_and_ps(r, _mm_castsi128_ps(_mm_set1_epi32(0x7FFFFFFF)));
    const __m128 ovf = _mm_cmpeq_ps(absr, _mm_castsi128_ps(_mm_set1_epi32(0x7F800000)));
    r = _mm_min_ps(_mm_max_ps(r, _mm_xor_ps(maxv, _mm_castsi128_ps(_mm_set1_epi32((int)0x80000000u)))), maxv);
    if (LIVE || v.m_flagsLive)
    {
        if (v.m_flagsLive)
        {
            const __m128 zero = _mm_cmpeq_ps(absr, _mm_setzero_ps());
            // Unterlauf: Ergebnis 0, aber nicht durch exakte Ausloeschung (Vorzeichen-Nullen sind bei FTZ exakt)
            __m128 exakt;
            const __m128 neg = _mm_castsi128_ps(_mm_set1_epi32((int)0x80000000u));
            if constexpr (K == 0) exakt = _mm_cmpeq_ps(s, _mm_xor_ps(t, neg));
            else if constexpr (K == 1) exakt = _mm_cmpeq_ps(s, t);
            else if constexpr (K == 2) exakt = _mm_or_ps(_mm_cmpeq_ps(s, _mm_setzero_ps()), _mm_cmpeq_ps(t, _mm_setzero_ps()));
            else if constexpr (K == 3) exakt = _mm_cmpeq_ps(a, _mm_xor_ps(p, neg));
            else exakt = _mm_cmpeq_ps(a, p);
            const __m128 und = _mm_andnot_ps(exakt, zero);
            const uint32_t zm = (uint32_t)_mm_movemask_ps(zero) & dm;
            const uint32_t sm = (uint32_t)_mm_movemask_ps(r) & dm;
            const uint32_t um = (uint32_t)_mm_movemask_ps(und) & dm;
            const uint32_t om = (uint32_t)_mm_movemask_ps(ovf) & dm;
            const uint32_t st = (zm ? 1u : 0u) | (sm ? 2u : 0u) | (um ? 4u : 0u) | (om ? 8u : 0u);
            if constexpr (LIVE)
            {
                const uint32_t mac = kVu5Rev[zm] | (kVu5Rev[sm] << 4) | (kVu5Rev[um] << 8) | (kVu5Rev[om] << 12);
                fr.push(v, v.m_cycle, mac, st);
            }
            else
                v.m_state.status |= st << 6;
        }
    }
    const __m128i lm = _mm_set_epi32((dm & 8) ? -1 : 0, (dm & 4) ? -1 : 0, (dm & 2) ? -1 : 0, (dm & 1) ? -1 : 0);
    const __m128 res = _mm_blendv_ps(_mm_loadu_ps(dst), r, _mm_castsi128_ps(lm));
    _mm_storeu_ps(dst, res);
    return res;
}
"""

FMAC5 = r'''
// Stufe 5: gemeinsamer FMAC-Helfer (SSE, alle 4 Lanes auf einmal, Ergebnis nur in die dest-Lanes).
// Rechnet bitgleich zur Inline-Fassung: Operanden normalisieren (Exponent 0 -> +-0, 255 -> +-Max), Ergebnis
// klassifizieren (Z/S/U/O je Lane, MAC- und Status-Bits wie der Interpreter) und klemmen.
static inline __m128 vu5Norm(__m128 x)
{
    const __m128i xi = _mm_castps_si128(x);
    const __m128i e = _mm_and_si128(xi, _mm_set1_epi32(0x7F800000));
    const __m128i sign = _mm_and_si128(xi, _mm_set1_epi32((int)0x80000000u));
    const __m128i isz = _mm_cmpeq_epi32(e, _mm_setzero_si128());
    const __m128i isf = _mm_cmpeq_epi32(e, _mm_set1_epi32(0x7F800000));
    __m128i r = _mm_blendv_epi8(xi, sign, isz);
    r = _mm_blendv_epi8(r, _mm_or_si128(sign, _mm_set1_epi32(0x7F7FFFFF)), isf);
    return _mm_castsi128_ps(r);
}
static const uint8_t kVu5Rev[16] = {0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15};
template <int K, int SRC, int TOACC, int LIVE, int STICKY>
void VU1Interpreter::Compiled::fmac5(VU1Interpreter &v, FlagRing &fr, uint64_t cyc, uint32_t fd, uint32_t fs, uint32_t ft, uint32_t bc, uint32_t dm)
{
    const __m128 s = vu5Norm(_mm_loadu_ps(v.m_state.vf[fs]));
    __m128 t;
    if constexpr (SRC == 0) t = _mm_set1_ps(vu1NormalizeOperandInline(v.m_state.vf[ft][bc]));
    else if constexpr (SRC == 1) t = _mm_set1_ps(vu1NormalizeOperandInline(v.m_state.q));
    else if constexpr (SRC == 2) t = _mm_set1_ps(vu1NormalizeOperandInline(v.m_state.i));
    else t = vu5Norm(_mm_loadu_ps(v.m_state.vf[ft]));
    __m128 r;
    if constexpr (K == 0) r = _mm_add_ps(s, t);
    else if constexpr (K == 1) r = _mm_sub_ps(s, t);
    else if constexpr (K == 2) r = _mm_mul_ps(s, t);
    else
    {
        const __m128 a = vu5Norm(_mm_loadu_ps(v.m_state.acc));
        r = (K == 3) ? _mm_add_ps(a, _mm_mul_ps(s, t)) : _mm_sub_ps(a, _mm_mul_ps(s, t));
    }
    const __m128i ri = _mm_castps_si128(r);
    const __m128i absv = _mm_and_si128(ri, _mm_set1_epi32(0x7FFFFFFF));
    const __m128i e = _mm_and_si128(ri, _mm_set1_epi32(0x7F800000));
    const __m128i sign = _mm_and_si128(ri, _mm_set1_epi32((int)0x80000000u));
    const __m128i zero = _mm_cmpeq_epi32(absv, _mm_setzero_si128());
    const __m128i e0 = _mm_cmpeq_epi32(e, _mm_setzero_si128());
    const __m128i und = _mm_andnot_si128(zero, e0);
    const __m128i ovf = _mm_cmpeq_epi32(e, _mm_set1_epi32(0x7F800000));
    __m128i c = _mm_blendv_epi8(ri, sign, und);
    c = _mm_blendv_epi8(c, _mm_or_si128(sign, _mm_set1_epi32(0x7F7FFFFF)), ovf);
    if (!LIVE)
    {
        // Flags dieses Befehls liest statisch niemand (vor dem Lesen von einem spaeteren FMAC verdeckt): nur die
        // Sticky-Bits nachfuehren, wie es das spaetere Eintragen getan haette. STICKY = 0: kein Programm liest
        // die Sticky-Bits (FSAND/FSOR-Masken ohne 0xFC0, kein FSEQ) -> entfaellt (wie microVU-Flag-Analyse).
        if (STICKY && v.m_flagsLive)
        {
            const uint32_t zm = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(_mm_or_si128(zero, und))) & dm;
            const uint32_t sm = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(ri)) & dm;
            const uint32_t um = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(und)) & dm;
            const uint32_t om = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(ovf)) & dm;
            const uint32_t st = (zm ? 1u : 0u) | (sm ? 2u : 0u) | (um ? 4u : 0u) | (om ? 8u : 0u);
            v.m_state.status |= st << 6;
        }
    }
    else if (v.m_flagsLive)
    {
        const uint32_t zm = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(_mm_or_si128(zero, und))) & dm;
        const uint32_t sm = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(ri)) & dm;
        const uint32_t um = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(und)) & dm;
        const uint32_t om = (uint32_t)_mm_movemask_ps(_mm_castsi128_ps(ovf)) & dm;
        const uint32_t mac = kVu5Rev[zm] | (kVu5Rev[sm] << 4) | (kVu5Rev[um] << 8) | (kVu5Rev[om] << 12);
        const uint32_t st = (zm ? 1u : 0u) | (sm ? 2u : 0u) | (um ? 4u : 0u) | (om ? 8u : 0u);
        fr.push(v, cyc, mac, st);
    }
    // nur die dest-Lanes schreiben
    const __m128i lm = _mm_set_epi32((dm & 8) ? -1 : 0, (dm & 4) ? -1 : 0, (dm & 2) ? -1 : 0, (dm & 1) ? -1 : 0);
    float *dst = TOACC ? v.m_state.acc : v.m_state.vf[fd];
    const __m128i old = _mm_castps_si128(_mm_loadu_ps(dst));
    _mm_storeu_ps(dst, _mm_castsi128_ps(_mm_blendv_epi8(old, c, lm)));
}
static inline void vu5Write(float *dst, __m128 val, uint32_t dm)
{
    const __m128i lm = _mm_set_epi32((dm & 8) ? -1 : 0, (dm & 4) ? -1 : 0, (dm & 2) ? -1 : 0, (dm & 1) ? -1 : 0);
    _mm_storeu_ps(dst, _mm_blendv_ps(_mm_loadu_ps(dst), val, _mm_castsi128_ps(lm)));
}
// ITOFn: Ganzzahl (rohe Bits) -> float / 2^n (Multiplikation mit 2^-n ist bitgleich zur Division)
template <int SH>
void VU1Interpreter::Compiled::itof5(VU1Interpreter &v, uint32_t ft, uint32_t fs, uint32_t dm)
{
    __m128 r = _mm_cvtepi32_ps(_mm_loadu_si128(reinterpret_cast<const __m128i *>(v.m_state.vf[fs])));
    if constexpr (SH != 0) r = _mm_mul_ps(r, _mm_set1_ps(1.0f / (float)(1 << SH)));
    vu5Write(v.m_state.vf[ft], r, dm);
}
// FTOIn: normalisiert, * 2^n (exakt), abschneiden, saettigen wie vuFloatToInt
template <int SH>
void VU1Interpreter::Compiled::ftoi5(VU1Interpreter &v, uint32_t ft, uint32_t fs, uint32_t dm)
{
    __m128 p = vu5Norm(_mm_loadu_ps(v.m_state.vf[fs]));
    if constexpr (SH != 0) p = _mm_mul_ps(p, _mm_set1_ps((float)(1 << SH)));
    __m128i i = _mm_cvttps_epi32(p);
    const __m128 big = _mm_cmpge_ps(p, _mm_set1_ps(2147483648.0f));
    i = _mm_blendv_epi8(i, _mm_set1_epi32(0x7FFFFFFF), _mm_castps_si128(big));
    vu5Write(v.m_state.vf[ft], _mm_castsi128_ps(i), dm);
}
void VU1Interpreter::Compiled::abs5(VU1Interpreter &v, uint32_t ft, uint32_t fs, uint32_t dm)
{
    const __m128 a = _mm_and_ps(vu5Norm(_mm_loadu_ps(v.m_state.vf[fs])), _mm_castsi128_ps(_mm_set1_epi32(0x7FFFFFFF)));
    vu5Write(v.m_state.vf[ft], a, dm);
}
// MAX/MINI: (vs > t) ? vs : t  bzw. (vs < t) ? vs : t  == maxps/minps(vs, t)
template <int ISMAX, int SRC>
void VU1Interpreter::Compiled::maxmin5(VU1Interpreter &v, uint32_t fd, uint32_t fs, uint32_t ft, uint32_t bc, uint32_t dm)
{
    const __m128 s = vu5Norm(_mm_loadu_ps(v.m_state.vf[fs]));
    __m128 t;
    if constexpr (SRC == 0) t = _mm_set1_ps(vu1NormalizeOperandInline(v.m_state.vf[ft][bc]));
    else if constexpr (SRC == 2) t = _mm_set1_ps(vu1NormalizeOperandInline(v.m_state.i));
    else t = vu5Norm(_mm_loadu_ps(v.m_state.vf[ft]));
    vu5Write(v.m_state.vf[fd], ISMAX ? _mm_max_ps(s, t) : _mm_min_ps(s, t), dm);
}
// CLIP: wie execUpper (rohe Bits, nicht normalisiert), Ergebnis in die Flag-Pipeline des Interpreters
void VU1Interpreter::Compiled::clip5(VU1Interpreter &v, ClipRing &cr, uint64_t cyc, uint32_t fs, uint32_t ft)
{
    uint32_t wBits; std::memcpy(&wBits, &v.m_state.vf[ft][3], 4);
    const int32_t limit = (wBits & 0x7F800000u) != 0u ? static_cast<int32_t>(wBits & 0x7FFFFFFFu) : 0x007FFFFF;
    uint32_t flags = 0u;
    for (int c = 0; c < 3; ++c)
    {
        uint32_t b; std::memcpy(&b, &v.m_state.vf[fs][c], 4);
        if ((int32_t)b > limit) flags |= 1u << (2 * c);
        if ((int32_t)(b ^ 0x80000000u) > limit) flags |= 2u << (2 * c);
    }
    // wie queueClip: Arbeitswert schieben, Eintrag wird 4 Takte spaeter sichtbar (lokaler Ring statt Pipeline)
    v.m_workingClip = ((v.m_workingClip << 6) | (flags & 0x3Fu)) & 0xFFFFFFu;
    cr.push(v, cyc, v.m_workingClip);
}
'''

def ready_expr(d):
    """calculatePairReadyCycle(decoded) mit konstanten Registern."""
    terms = []
    for u in (d.uu, d.lu):
        for reg, lanes in u.vfreads():
            for c in range(4):
                if lanes & LANE[c]:
                    terms.append("v.m_vfReady[%d][%d]" % (reg, c))
        bits = u.viRead & 0xFFFE
        for reg in range(16):
            if bits & (1 << reg):
                terms.append("v.m_viReady[%d]" % reg)
        for c in range(4):
            if u.accRead & LANE[c]:
                terms.append("v.m_accReady[%d]" % c)
    lines = ["uint64_t r = v.m_cycle;"]
    seen = set()
    for t in terms:
        if t not in seen:
            seen.add(t)
            lines.append("if (%s > r) r = %s;" % (t, t))
    lu = d.lu
    if lu.pipe == PIPE_FDIV:
        lines.append("if (v.m_fdiv.valid && v.m_fdiv.readyCycle > r) r = v.m_fdiv.readyCycle;")
    if lu.pipe == PIPE_EFU:
        lines.append("if (v.m_efuResourceReady > r) r = v.m_efuResourceReady;")
    if lu.waitQ:
        lines.append("if (v.m_fdiv.valid && v.m_fdiv.readyCycle > r) r = v.m_fdiv.readyCycle;")
    if lu.waitP:
        lines.append("for (const auto &e : v.m_efu) if (e.valid && e.readyCycle > r) r = e.readyCycle;")
    if lu.pipe == PIPE_XGKICK:
        lines.append("if (v.m_xgkick.active && v.m_cycle + 1u > r) r = v.m_cycle + 1u;")
    lines.append("return r;")
    return lines

def gen_pair(d, out):
    w = out.append
    w("        case 0x%04x: L_%04x: {" % (d.pc, d.pc))
    w("            if (!(v.m_cycle < budgetEnd) || v.m_stopRequested) return 0;")
    if STUFE < 2:
        w("            v.commitReadyPipelines();")
    # Stufe 2: der Commit am Paaranfang ist doppelt (advanceOneCycle des Vorgaengers hat im selben Zyklus schon
    # committet, neue Eintraege sind immer spaeter fertig) -> nur einmal beim Eintritt in die Funktion.
    if d.uu.reserved or d.lu.reserved:
        w("            return 1; // reservierter Befehl: Interpreter meldet ihn")
        w("        }")
        return
    calc = ready_expr(d)
    simple = len(calc) == 2  # nur "r = m_cycle; return r"
    if not simple:
        w("            auto calc = [&]() -> uint64_t { %s };" % " ".join(calc))
        w("            uint64_t ready = calc();")
        w("            while (ready > v.m_cycle)")
        w("            {")
        w("                if (ready >= budgetEnd) { v.advanceTo(budgetEnd); break; }")
        w("                v.advanceTo(ready);")
        w("                ready = calc();")
        w("            }")
        w("            if (v.m_cycle >= budgetEnd) return 0;")
    # writtenVi: erstes VI 1..15 in lowerUsage.viWrite
    W = next((r for r in range(1, 16) if d.lu.viWrite & (1 << r)), 0)
    UW, UWL = d.uu.wreg, d.uu.wlanes
    LW, LWL = d.lu.wreg, d.lu.wlanes
    hasUpperWrite = UW != 0
    hasLowerWrite = LW != 0 and d.suppressed != LW
    hasDistinctLowerWrite = hasLowerWrite and (not hasUpperWrite or LW != UW)
    if W:
        w("            const int32_t oldVi = v.m_state.vi[%d];" % W)
    if STUFE >= 2:
        gen_exec_direct(d, w)
    else:
        gen_exec_exact(d, w, W, hasUpperWrite, hasDistinctLowerWrite, UW, UWL, LW, LWL)
    # markPairWrites
    gen_mark(d, w, W, hasUpperWrite, hasLowerWrite, UW, UWL, LW, LWL)
    gen_tail(d, w)

def gen_exec_direct(d, w):
    """Stufe 2: Ergebnisse sofort schreiben. Leser warten ohnehin (Wartezyklen), bis der Wert fertig waere."""
    U, L = d.upper, d.lower
    if d.iBit:
        ui = upper_inline(U)
        for line in (ui if ui is not None else ["v.execUpper(0x%08xu);" % U]):
            w("            " + line)
        w("            { const uint32_t bits = 0x%08xu; float imm; std::memcpy(&imm, &bits, 4); v.m_state.i = vu1NormalizeOperandInline(imm); }" % L)
    elif d.shadow:
        S = d.shadow
        w("            { float oldVf[4], upperVf[4]; std::memcpy(oldVf, v.m_state.vf[%d], 16);" % S)
        w("              v.execUpper(0x%08xu); std::memcpy(upperVf, v.m_state.vf[%d], 16); std::memcpy(v.m_state.vf[%d], oldVf, 16);" % (U, S, S))
        w("              v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu); std::memcpy(v.m_state.vf[%d], upperVf, 16); }" % (L, U, S))
    else:
        ui = upper_inline(U)
        for line in (ui if ui is not None else ["v.execUpper(0x%08xu);" % U]):
            w("            " + line)
        li = lower_inline(L, d.pc)
        for line in (li if li is not None else ["v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu);" % (L, U)]):
            w("            " + line)
    w("            v.m_viBranchBackupValid = false;")

def gen_exec_exact(d, w, W, hasUpperWrite, hasDistinctLowerWrite, UW, UWL, LW, LWL):
    U, L = d.upper, d.lower
    if hasUpperWrite:
        w("            float oldUpperVf[4]; std::memcpy(oldUpperVf, v.m_state.vf[%d], 16);" % UW)
    if hasDistinctLowerWrite:
        w("            float oldLowerVf[4]; std::memcpy(oldLowerVf, v.m_state.vf[%d], 16);" % LW)
    if d.uu.accWrite:
        w("            float oldAcc[4]; std::memcpy(oldAcc, v.m_state.acc, 16);")
    U, L = d.upper, d.lower
    if d.iBit:
        w("            v.execUpper(0x%08xu);" % U)
        w("            { const uint32_t bits = 0x%08xu; float imm; std::memcpy(&imm, &bits, 4); v.m_state.i = v.normalizeOperand(imm); }" % L)
    elif d.shadow:
        S = d.shadow
        w("            {")
        w("                float oldVf[4], upperVf[4];")
        w("                std::memcpy(oldVf, v.m_state.vf[%d], 16);" % S)
        w("                v.execUpper(0x%08xu);" % U)
        w("                std::memcpy(upperVf, v.m_state.vf[%d], 16);" % S)
        w("                std::memcpy(v.m_state.vf[%d], oldVf, 16);" % S)
        w("                v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu);" % (L, U))
        w("                std::memcpy(v.m_state.vf[%d], upperVf, 16);" % S)
        w("            }")
    else:
        w("            v.execUpper(0x%08xu);" % U)
        w("            v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu);" % (L, U))
    w("            v.m_viBranchBackupValid = false;")
    if hasUpperWrite:
        lat = d.uu.vfLat or d.uu.lat
        w("            { float nv[4]; std::memcpy(nv, v.m_state.vf[%d], 16); std::memcpy(v.m_state.vf[%d], oldUpperVf, 16); v.queueVfWrite(%d, %d, nv, %d); }" % (UW, UW, UW, UWL, lat))
    if hasDistinctLowerWrite:
        lat = d.lu.vfLat or d.lu.lat
        w("            { float nv[4]; std::memcpy(nv, v.m_state.vf[%d], 16); std::memcpy(v.m_state.vf[%d], oldLowerVf, 16); v.queueVfWrite(%d, %d, nv, %d); }" % (LW, LW, LW, LWL, lat))
    if d.uu.accWrite:
        w("            { float na[4]; std::memcpy(na, v.m_state.acc, 16); std::memcpy(v.m_state.acc, oldAcc, 16); v.queueAccWrite(%d, na, 1); }" % d.uu.accWrite)
    if W:
        lat = d.lu.viLat or d.lu.lat
        w("            { const int32_t nvi = v.m_state.vi[%d]; v.m_state.vi[%d] = oldVi; v.queueViWrite(%d, nvi, %d); }" % (W, W, W, lat))

def rec_lat(d):
    """groesste Ergebnis-Latenz eines Paars (fuer m_recPend: Programmende wartet darauf), 0 = keine"""
    W = next((r for r in range(1, 16) if d.lu.viWrite & (1 << r)), 0)
    UW, LW = d.uu.wreg, d.lu.wreg
    hasUpperWrite = UW != 0
    hasLowerWrite = LW != 0 and d.suppressed != LW
    lats = []
    if hasUpperWrite: lats.append(d.uu.vfLat or d.uu.lat)
    if hasLowerWrite and (not hasUpperWrite or LW != UW): lats.append(d.lu.vfLat or d.lu.lat)
    if d.uu.accWrite: lats.append(1)
    if W: lats.append(d.lu.viLat or d.lu.lat)
    return max(lats) if lats else 0

RECPEND_DEAD = set()  # Stufe 5: Paare, deren m_recPend-Aktualisierung von einem spaeteren Paar im Block sicher ueberboten wird

def analyse_dist(pairs, entries, exits):
    """Abstand (in Paaren) jedes Paars zum naechsten moeglichen Ausgang seines geradlinigen Abschnitts: Verzoegerungs-
    paar eines Sprungs, reserviertes Paar, Luecke, oder das letzte Paar vor einem Einstieg (dort prueft der Nachfolger
    zur Laufzeit). Ein Schreibzugriff mit Latenz L ist an der Grenze sicher fertig, wenn Abstand >= L-1."""
    DIST_EXIT.clear()
    run = []; lastpc = None
    def done():
        n = len(run); nx = None
        for idx in range(n - 1, -1, -1):
            if idx == n - 1 or run[idx] in exits:
                nx = idx
            DIST_EXIT[run[idx]] = nx - idx
        run.clear()
    for p in pairs:
        if p.pc in entries or lastpc is None or p.pc != ((lastpc + 8) & 0x3FFF):
            done()
        run.append(p.pc); lastpc = p.pc
    done()

def analyse_inflight(pairs, entries, exits, prev):
    """Stufe 2 (wie microVU-Pipeline-Zustand): welche Register koennen an einem Einstieg noch unterwegs sein?
    Aus jedem Ausgang (Verzoegerungspaar k) kommen die Schreibzugriffe der letzten Paare mit (k-j+1) < Latenz.
    Statische Spruenge -> ihr Ziel; Weiterlaufen -> der folgende Einstieg; JR/JALR -> alle Einstiege (Ziel unbekannt).
    Programmstart und Wiedereinstieg aus dem Interpreter bringen nichts mit (Scheduler geleert bzw. Laufzeitpruefung)."""
    INFLIGHT.clear()
    for e in entries:
        INFLIGHT[e] = set()
    bypc = {p.pc: p for p in pairs}
    # Schreibzugriffe je Paar: (schluessel, latenz)
    writes = {p.pc: pair_rw(p)[1] for p in pairs}
    def tail_inflight(k):
        out = set(); pc = k; n = 0
        while pc in bypc and n < 16:
            for key, L in writes[pc]:
                if n + 1 < L:
                    out.add(key)
            n += 1
            prv = (pc - 8) & 0x3FFF
            if pc in entries or prv not in bypc:
                break
            pc = prv
        return out
    dyn = set()
    lastpc = None
    order = sorted(bypc)
    for idx, pc in enumerate(order):
        p = bypc[pc]
        c = lower_ctrl(p)
        slot = (pc + 8) & 0x3FFF
        nxt_after_slot = (pc + 16) & 0x3FFF
        if c and slot in bypc:
            fl = tail_inflight(slot)
            if c[0] == "static":
                if c[2] in INFLIGHT: INFLIGHT[c[2]] |= fl
                if c[1] != "B" and nxt_after_slot in INFLIGHT: INFLIGHT[nxt_after_slot] |= fl  # bedingt nicht genommen / BAL-Ruecksprung
            else:
                dyn |= fl
                if c[1] == "JALR" and nxt_after_slot in INFLIGHT: INFLIGHT[nxt_after_slot] |= fl
        # Weiterlaufen in einen Einstieg: immer, ausser pc ist Verzoegerungspaar eines unbedingten Sprungs (B/BAL/JR/JALR)
        # oder eines Programmendes (danach startet das Programm neu: nichts unterwegs)
        nxt = (pc + 8) & 0x3FFF
        if nxt in INFLIGHT:
            k = prev.get(pc)
            flows = True
            if k is not None:
                if k[0] == "static":
                    c0 = lower_ctrl(bypc[(pc - 8) & 0x3FFF]) if ((pc - 8) & 0x3FFF) in bypc else None
                    flows = bool(c0) and c0[1] not in ("B", "BAL")
                else:
                    flows = False
            if flows:
                INFLIGHT[nxt] |= tail_inflight(pc)
    for e in INFLIGHT:
        INFLIGHT[e] |= dyn

def analyse_recpend(pairs, entries, exits):
    """Zwischen Paar i und einem spaeteren Paar j desselben geradlinigen Abschnitts vergehen mindestens j-i Takte.
    Gilt lat_j + (j-i) >= lat_i, liegt cycle_j + lat_j sicher nicht unter cycle_i + lat_i: die Speicherung bei i ist
    ueberfluessig. Abschnitte enden an Einstiegen, Luecken und moeglichen Ausgaengen."""
    RECPEND_DEAD.clear()
    seg = []
    def done():
        best = -10**9  # max ueber spaetere j von (lat_j + j)
        for idx in range(len(seg) - 1, -1, -1):
            pc, L = seg[idx]
            if L and best - idx >= L:
                RECPEND_DEAD.add(pc)
            if L:
                best = max(best, L + idx)
        seg.clear()
    lastpc = None
    for p in pairs:
        if p.pc in entries or lastpc is None or p.pc != ((lastpc + 8) & 0x3FFF):
            done()
        seg.append((p.pc, rec_lat(p)))
        if p.pc in exits:
            done()
        lastpc = p.pc
    done()

def gen_mark(d, w, W, hasUpperWrite, hasLowerWrite, UW, UWL, LW, LWL):
    statik = STATIK and STUFE >= 5 and FLAG_TARGET == "ring"
    dist = DIST_EXIT.get(d.pc, 0)
    def mark_needed(key, lat):
        if not statik:
            return True
        return dist < lat - 1 or (d.pc, key) in MARK_USED
    if STUFE >= 2:
        # Interpreter haette diese Schreibvorgaenge bis zu ihrem Fertig-Zyklus in der Warteschlange (Programmende wartet darauf)
        L = rec_lat(d)
        if L and not (STUFE >= 5 and FLAG_TARGET == "ring" and d.pc in RECPEND_DEAD) and (not statik or dist < L - 1):
            w("            if (v.m_cycle + %du > v.m_recPend) v.m_recPend = v.m_cycle + %du;" % (L, L))
    if hasLowerWrite:
        lat = d.lu.vfLat or d.lu.lat
        for c in range(4):
            if LWL & LANE[c] and mark_needed("v.m_vfReady[%d][%d]" % (LW, c), lat):
                w("            v.m_vfReady[%d][%d] = v.m_cycle + %d;" % (LW, c, lat))
    if hasUpperWrite:
        lat = d.uu.vfLat or d.uu.lat
        for c in range(4):
            if UWL & LANE[c] and mark_needed("v.m_vfReady[%d][%d]" % (UW, c), lat):
                w("            v.m_vfReady[%d][%d] = v.m_cycle + %d;" % (UW, c, lat))
    for reg in range(1, 16):
        if d.lu.viWrite & (1 << reg):
            lat = d.lu.viLat or d.lu.lat
            if mark_needed("v.m_viReady[%d]" % reg, lat):
                w("            v.m_viReady[%d] = v.m_cycle + %d;" % (reg, lat))
    for c in range(4):
        if d.uu.accWrite & LANE[c] and mark_needed("v.m_accReady[%d]" % c, 1):
            w("            v.m_accReady[%d] = v.m_cycle + 1;" % c)
    if W and d.lu.delayBr:
        if FLAG_TARGET == "ring":
            w("            v.m_viBranchBackupValue = oldVi; v.m_viBranchBackupReg = %d; v.m_viBranchBackupValid = true;" % W)
        else:
            w("            v.recordViWriteForBranch(%d, oldVi);" % W)

def gen_tail(d, w):
    w("            v.m_state.vf[0][0] = 0.0f; v.m_state.vf[0][1] = 0.0f; v.m_state.vf[0][2] = 0.0f; v.m_state.vf[0][3] = 1.0f; v.m_state.vi[0] = 0;")
    nxt = d.pc + 8
    w("            v.m_state.pc = (0x%04xu >= codeSize) ? 0u : 0x%04xu;" % (nxt, nxt))
    w("            if (v.m_state.branchPending)")
    w("            {")
    w("                if (v.m_state.branchDelay == 0u) { v.m_state.pc = v.m_state.branchTarget & v.microAddressMask(); v.m_state.branchPending = false; }")
    w("                else --v.m_state.branchDelay;")
    w("            }")
    # Halt-Logik (Bits konstant)
    haltBranch = d.lu.pipe == PIPE_BRANCH
    w("            {")
    w("                const bool dHalt = %s;" % ("v.m_state.dBitEnabled" if d.dBit else "false"))
    w("                const bool tHalt = %s;" % ("v.m_state.tBitEnabled" if d.tBit else "false"))
    w("                const bool haltBit = dHalt || tHalt;")
    w("                const bool haltBranch = haltBit && %s;" % ("true" if haltBranch else "false"))
    w("                if (v.m_state.haltAfterDelaySlot) { v.m_state.stoppedByD = v.m_pendingHaltD; v.m_state.stoppedByT = v.m_pendingHaltT; programEnded = true; }")
    w("                else if (v.m_state.ebit) programEnded = true;")
    w("                else if (haltBit && !haltBranch) { v.m_state.stoppedByD = dHalt; v.m_state.stoppedByT = tHalt; programEnded = true; }")
    if d.eBit:
        w("                else if (true) v.m_state.ebit = true;")
    w("                else if (haltBranch) { v.m_state.haltAfterDelaySlot = true; v.m_pendingHaltD = dHalt; v.m_pendingHaltT = tHalt; }")
    w("            }")
    w("            v.advanceOneCycle();")
    w("            if (programEnded) return 0;")
    if STUFE >= 2 and (d.pc + 8) in COVERED:
        w("            if (v.m_state.pc == 0x%04xu) goto L_%04x;" % (d.pc + 8, d.pc + 8))
    w("            break;")
    w("        }")

def main():
    d, outp = sys.argv[1], sys.argv[2]
    minpct = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    global STUFE
    STUFE = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    cyc = collections.Counter()
    for line in open(os.path.join(d, "vu1_index.txt")):
        h, pc, calls, c = line.split()
        cyc[h] += int(c)
        START_PCS.setdefault(h, set()).add(int(pc, 16))
    tot = sum(cyc.values()) or 1
    progs = []
    for h, c in cyc.most_common():
        if 100.0 * c / tot < minpct:
            continue
        dec = os.path.join(d, "vu1_%s.dec" % h)
        cov = os.path.join(d, "vu1_%s.cov" % h)
        if not (os.path.exists(dec) and os.path.exists(cov)):
            print("  uebersprungen %s (keine .dec/.cov)" % h)
            continue
        covered = {int(x, 16) for x in open(cov).read().split()}
        pairs = [Pair(l) for l in open(dec) if l.strip()]
        if os.environ.get("VU1REC_STATISCH", "1") == "1":
            n0 = len(covered)
            code0 = open(os.path.join(d, "vu1_%s.bin" % h), "rb").read()
            tab = jump_table(code0)
            START_PCS.setdefault(h, set()).update(tab)
            lenf = os.path.join(d, "vu1_%s.len" % h)
            modlen = int(open(lenf).read().split()[0]) if os.path.exists(lenf) else 0
            covered = static_reach(pairs, covered | START_PCS.get(h, set()), modlen)
            if len(covered) != n0:
                print("  %s: statisch erreichbar %d Paare (Mitschnitt %d)" % (h, len(covered), n0))
        pairs = [p for p in pairs if p.pc in covered]
        # Liest ein tatsaechlich ausgefuehrter Befehl MAC/Status? (Der ganze Mikrospeicher enthaelt auch Reste
        # anderer Programme; die zaehlen nicht. Nicht abgedeckte Stellen schalten Flags beim Rueckfall wieder ein.)
        code = open(os.path.join(d, "vu1_%s.bin" % h), "rb").read()
        live = False
        for off in sorted(covered):
            if off + 8 > len(code):
                continue
            lo, up = struct.unpack_from("<II", code, off)
            if (up >> 31) or (lo >> 31):
                continue
            if (lo >> 25) in (0x14, 0x16, 0x17, 0x18, 0x1A, 0x1B):
                live = True
                break
        if STUFE >= 5 and any(not (p.lower >> 31) and (p.lower >> 25) == 0x11 for p in pairs):
            print("  %s: enthaelt FCSET -> nicht uebersetzt (Clip-Ring bildet FCSET nicht nach), laeuft im Interpreter" % h)
            continue
        # Erkennungssignatur: die Bytes genau der uebersetzten Paare (Reste anderer Module im Speicher zaehlen nicht)
        cset = sorted(p.pc for p in pairs)
        rng = []
        for pc in cset:
            if rng and rng[-1][1] == pc:
                rng[-1][1] = pc + 8
            else:
                rng.append([pc, pc + 8])
        hv = 1469598103934665603
        for a, b in rng:
            for x in code[a:b]:
                hv = ((hv ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        SIGS[h] = (rng, hv)
        progs.append((h, pairs, c, live))
    # Liest irgendein Programm die Sticky-Status-Bits (0xFC0)? Der Status bleibt zwischen Programmen erhalten,
    # deshalb ueber alle Programme. FSEQ vergleicht den ganzen Status -> braucht sie immer.
    global NEED_STICKY
    NEED_STICKY = False
    for h, pairs, c, live in progs:
        for p in pairs:
            lo = p.lower
            if lo >> 31:
                continue
            op = lo >> 25
            imm12 = (((lo >> 21) & 1) << 11) | (lo & 0x7FF)
            if op == 0x14 or (op in (0x16, 0x17) and (imm12 & 0xFC0)):
                NEED_STICKY = True
    if os.environ.get("VU1REC_STICKY") == "1":
        NEED_STICKY = True  # erzwingen (A/B-Vergleich)
    print("  Sticky-Status-Bits werden %s" % ("gefuehrt" if NEED_STICKY else "von keinem Programm gelesen -> entfallen"))
    global STUFE3_OK
    STUFE3_OK = set()
    if STUFE >= 3:
        for h, pairs, c, live in progs:
            ok, why = program3_ok(pairs)
            if ok:
                STUFE3_OK.add(h)
            else:
                print("  %s: Stufe 2 (%s)" % (h, why))
    out = []
    w = out.append
    w("// ERZEUGT von tools/vu1rec.py - nicht von Hand bearbeiten.")
    w("// Vorab uebersetzte VU1-Mikroprogramme von SotC (Stufe 1: exakt wie VU1Interpreter::run, Dekodierung aufgeloest).")
    w('#include "runtime/ps2_vu1.h"')
    w('#include "runtime/ps2_memory.h"')
    w('#include "runtime/gs/gs_frontend.h"')
    w('#include "ps2_vu1_detail.h"')
    w("#include <cstddef>")
    w("#include <cstdint>")
    w("#include <cstring>")
    w("#include <cstdlib>")
    w("#include <cstdio>")
    w("#include <cmath>")
    w("#include <mutex>")
    w("#include <unordered_map>")
    w("#include <limits>")
    w("#include <immintrin.h>")
    w("")
    w("static inline bool vu1Fast() { static const bool f = [] { const char *e = std::getenv(\"PS2X_VU1_FAST\"); return e && *e && *e != '0'; }(); return f; }")
    w("static bool flagsAlwaysOn() { static const bool on = [] { const char *e = std::getenv(\"PS2X_VU1_FLAGS_ALWAYS\"); return e && *e && *e != '0'; }(); return on; }")
    w("")
    w("struct VU1Interpreter::Compiled")
    w("{")
    w("    struct FlagRing;")
    if STUFE >= 5:
        w("    template <int K, int SRC, int TOACC, int LIVE, int STICKY> static __attribute__((always_inline)) inline void fmac5(VU1Interpreter &v, FlagRing &fr, uint64_t cyc, uint32_t fd, uint32_t fs, uint32_t ft, uint32_t bc, uint32_t dm);")
        w("    template <int SH> static __attribute__((always_inline)) inline void itof5(VU1Interpreter &v, uint32_t ft, uint32_t fs, uint32_t dm);")
        w("    template <int SH> static __attribute__((always_inline)) inline void ftoi5(VU1Interpreter &v, uint32_t ft, uint32_t fs, uint32_t dm);")
        w("    static __attribute__((always_inline)) inline void abs5(VU1Interpreter &v, uint32_t ft, uint32_t fs, uint32_t dm);")
        w("    template <int ISMAX, int SRC> static __attribute__((always_inline)) inline void maxmin5(VU1Interpreter &v, uint32_t fd, uint32_t fs, uint32_t ft, uint32_t bc, uint32_t dm);")
        w("    struct ClipRing;")
        w("    static __attribute__((always_inline)) inline void clip5(VU1Interpreter &v, ClipRing &cr, uint64_t cyc, uint32_t fs, uint32_t ft);")
    if STUFE >= 6:
        w("    template <int K, int LIVE> static __attribute__((always_inline)) inline __m128 fmac6(VU1Interpreter &v, FlagRing &fr, __m128 s, __m128 t, __m128 a, float *dst, uint32_t dm);")
    for h, pairs, c, live in progs:
        w("    static int run_%s(VU1Interpreter &v, uint8_t *vuCode, uint32_t codeSize, uint8_t *vuData, uint32_t dataSize, GS &gs, PS2Memory *memory, uint64_t budgetEnd, bool &programEnded);" % h)
    w("};")
    w(FLAGRING.replace("struct FlagRing", "struct VU1Interpreter::Compiled::FlagRing", 1).replace("static void apply(", "static void apply(", 1))
    if STUFE >= 5:
        w(CLIPRING)
    w("void VU1Interpreter::Compiled::FlagRing::apply(VU1Interpreter &v, uint32_t mac, uint32_t st)")
    w("{")
    w("    // wie commitReadyPipelines fuer einen FMAC-Eintrag (writesMac, writesStatus, extraSticky = 0)")
    w("    v.m_state.mac = mac;")
    w("    const uint32_t current = st & 0xFu;")
    w("    v.m_state.status = (v.m_state.status & 0xFF0u) | current | (current << 6);")
    w("}")
    w("")
    if STUFE >= 5:
        w(FMAC5)
    if STUFE >= 6:
        w(FMAC6)
    for h, pairs, c, live in progs:
        w("// Programm %s: %d Paare, %.1f %% der VU1-Zyklen im Mitschnitt" % (h, len(pairs), 100.0 * c / tot))
        w("int VU1Interpreter::Compiled::run_%s(VU1Interpreter &v, uint8_t *vuCode, uint32_t codeSize, uint8_t *vuData, uint32_t dataSize, GS &gs, PS2Memory *memory, uint64_t budgetEnd, bool &programEnded)" % h)
        w("{")
        w("    (void)vuCode;")
        w("    if (!flagsAlwaysOn()) v.m_flagsLive = %s;" % ("true" if live else "false"))
        if STUFE >= 3 and h in STUFE3_OK:
            w("    if (vu1Fast())")
            w("    {")
            gen_program3(h, pairs, out)
            w("    }")
        if STUFE >= 2:
            w("    v.commitReadyPipelines();")
        w("    for (;;)")
        w("    {")
        w("        switch (v.m_state.pc)")
        w("        {")
        global COVERED
        COVERED = {p.pc for p in pairs}
        for p in pairs:
            gen_pair(p, out)
        w("        default:")
        w("            v.m_flagsLive = true; // unbekannter Code koennte Flags lesen")
        w("            return 1; // nicht abgedeckt -> Interpreter")
        w("        }")
        w("    }")
        w("}")
        w("")
    w("extern const VU1Interpreter::CompiledEntry g_vu1CompiledTable[] = {")
    for h, pairs, c, live in progs:
        w("    {0x%sull, &VU1Interpreter::Compiled::run_%s}," % (h, h))
    if not progs:
        w("    {0ull, nullptr},")
    w("};")
    w("extern const size_t g_vu1CompiledCount = %d;" % len(progs))
    w("// Sticky-Status-Bits (0xFC0) werden in den uebersetzten Programmen nicht gefuehrt, weil kein Programm sie liest:")
    w("extern const uint32_t g_vu1CompiledStatusIgnore = 0x%03xu;" % (0 if NEED_STICKY else 0xFC0))
    # Einstiegskarten: der Interpreter springt nach einem Rueckfall am naechsten Einstieg zurueck in den uebersetzten Code
    for h, pairs, c, live in progs:
        bits = [0] * 2048
        for e in ENTRY_MAPS.get(h, ()):
            bits[e // 8] = 1
        w("static const uint8_t s_entry_%s[2048] = {%s};" % (h, ",".join(str(b) for b in bits)))
    w("extern const uint8_t *vu1CompiledEntryMap(uint64_t hash)")
    w("{")
    for h, pairs, c, live in progs:
        w("    if (hash == 0x%sull) return s_entry_%s;" % (h, h))
    w("    return nullptr;")
    w("}")
    # Erkennung ueber Signaturen: passt der Speicher in allen uebersetzten Bereichen, ist der Code gueltig, egal was
    # sonst im Mikrospeicher steht (Reste vorher geladener Module, nachgeladene Teile)
    for h, pairs, c, live in progs:
        rng, hv = SIGS[h]
        w("static const uint16_t s_rng_%s[] = {%s};" % (h, ",".join("%d,%d" % (a, b) for a, b in rng)))
    w("struct Vu1Sig { uint64_t hash; uint64_t sig; const uint16_t *rng; uint32_t nrng; VU1Interpreter::CompiledFn fn; const uint8_t *entry; };")
    w("static const Vu1Sig s_sigs[] = {")
    for h, pairs, c, live in progs:
        rng, hv = SIGS[h]
        w("    {0x%sull, 0x%016xull, s_rng_%s, %du, &VU1Interpreter::Compiled::run_%s, s_entry_%s}," % (h, hv, h, len(rng), h, h))
    w("};")
    w("extern VU1Interpreter::CompiledFn vu1CompiledMatch(const uint8_t *code, uint32_t size, uint64_t whole, const uint8_t **entry)")
    w("{")
    w("    static std::mutex m; static std::unordered_map<uint64_t, const Vu1Sig *> cache;")
    w("    std::lock_guard<std::mutex> lk(m);")
    w("    auto it = cache.find(whole);")
    w("    const Vu1Sig *hit = nullptr;")
    w("    if (it != cache.end()) hit = it->second;")
    w("    else")
    w("    {")
    w("        for (const Vu1Sig &s : s_sigs) if (s.hash == whole) { hit = &s; break; }")
    w("        if (!hit)")
    w("            for (const Vu1Sig &s : s_sigs)")
    w("            {")
    w("                uint64_t h = 1469598103934665603ull; bool fits = true;")
    w("                for (uint32_t k = 0; k < s.nrng && fits; ++k)")
    w("                {")
    w("                    const uint32_t a = s.rng[2 * k], b = s.rng[2 * k + 1];")
    w("                    if (b > size) { fits = false; break; }")
    w("                    for (uint32_t i = a; i < b; ++i) { h ^= code[i]; h *= 1099511628211ull; }")
    w("                }")
    w("                if (fits && h == s.sig) { hit = &s; break; }")
    w("            }")
    w("        cache.emplace(whole, hit);")
    w("        if (hit && hit->hash != whole) std::fprintf(stderr, \"[VU1-Rec] Speicherabbild %016llx -> uebersetztes Programm %016llx (Signatur)\\n\", (unsigned long long)whole, (unsigned long long)hit->hash);")
    w("    }")
    w("    if (entry) *entry = hit ? hit->entry : nullptr;")
    w("    return hit ? hit->fn : nullptr;")
    w("}")
    teile = int(os.environ.get("VU1REC_TEILE", "0"))
    if teile > 0:
        # Aufteilen fuer paralleles Kompilieren: Vorspann (Klasse, Helfer) in jede Teildatei, die run_-Funktionen
        # reihum auf VU1REC_TEILE Dateien, Tabellen in die Hauptdatei. Immer alle Teile schreiben (keine Altlasten).
        starts = [k for k, l in enumerate(out) if l.startswith("int VU1Interpreter::Compiled::run_")]
        tail = next(k for k, l in enumerate(out) if l.startswith("extern const VU1Interpreter::CompiledEntry g_vu1CompiledTable"))
        pre = [l.replace("void VU1Interpreter::Compiled::FlagRing::apply(", "inline void VU1Interpreter::Compiled::FlagRing::apply(") for l in out[:starts[0]]] if starts else out[:tail]
        bodies = [out[a:b] for a, b in zip(starts, starts[1:] + [tail])]
        groups = [[] for _ in range(teile)]
        for i, b in enumerate(sorted(bodies, key=len, reverse=True)):
            min(groups, key=lambda g: sum(len(x) for x in g)).append(b)  # nach Groesse verteilen
        for t, g in enumerate(groups):
            with open(outp.replace(".cpp", "_t%02d.cpp" % t), "w") as fo:
                fo.write("// Teil %d von %d (tools/vu1rec.py, VU1REC_TEILE)\n" % (t, teile))
                fo.write("\n".join(pre) + "\n")
                for b in g:
                    fo.write("\n".join(b) + "\n")
        out = pre + out[tail:]
    open(outp, "w").write("\n".join(out) + "\n")
    print("%d Programme, %d Paare -> %s" % (len(progs), sum(len(p) for _, p, _, _ in progs), outp))



# ===================================================================================================================
# Stufe 3: wie PCSX2 microVU - Programmfluss statisch (Spruenge als goto, Verzoegerungsplaetze bekannt), kein Takt-fuer-
# Takt-Aufruf mehr (nur ++m_cycle und Commit, wenn die Interpreter-Warteschlangen etwas faellig haben), XGKICK sofort
# komplett, FMAC-Flags in einem kleinen Ring, der erst beim Lesen (FS*/FM*) oder am Ende eingetragen wird.
# ===================================================================================================================
BR_STATIC = {0x20: "B", 0x21: "BAL", 0x28: "IBEQ", 0x29: "IBNE", 0x2C: "IBLTZ", 0x2D: "IBGTZ", 0x2E: "IBLEZ", 0x2F: "IBGEZ"}
BR_DYN = {0x24: "JR", 0x25: "JALR"}
FLAG_READERS = {0x14, 0x16, 0x17, 0x18, 0x1A, 0x1B}

def jump_table(code):
    """Sprungtabelle am Modulanfang (SotC: je 16 Byte 'B ziel' + NOP-Paar, Luecken mit NOP/NOP gefuellt).
    Ihre Eintraege sind die Startadressen, die das Spiel per MSCAL benutzt."""
    out = [0]
    pc = 0
    while pc + 16 <= min(len(code), 0x800):
        lo, up = struct.unpack_from("<II", code, pc)
        lo2, up2 = struct.unpack_from("<II", code, pc + 8)
        nop2 = (up2 & 0x3F) == 0x3F and (up2 >> 27) == 0 and (lo2 == 0x8000033C or lo2 == 0)
        if (lo >> 31) == 0 and ((lo >> 25) & 0x7F) == 0x20 and nop2 and (up >> 27) == 0:
            out.append(pc)
        elif (lo == 0x8000033C or lo == 0) and (up & 0x3F) == 0x3F and (up >> 27) == 0 and nop2:
            pass  # Fuellpaare
        else:
            break
        pc += 16
    return out

def static_reach(pairs, seeds, modlen=0):
    """Wie microVU: alle von bekannten Einstiegen aus erreichbaren Paare (beide Seiten bedingter Spruenge, Sprungziele,
    Ruecksprungadressen von BAL/JALR). Ziele von JR/JALR sind dynamisch: dafuer gelten alle Ruecksprungadressen und die
    Einstiege des Mitschnitts; was dann noch fehlt, faengt der Wiedereinstieg zur Laufzeit ab. Am Programmende (E-Bit)
    wird nur das Verzoegerungspaar mitgenommen; der Code dahinter zaehlt erst, wenn er als Einstieg bekannt ist."""
    bypc = {p.pc: p for p in pairs}
    def ok(pc):
        p = bypc.get(pc)
        return p is not None and not (p.uu.reserved or p.lu.reserved)
    def crosses(a, b):
        # Weiterlaufen ueber das Modulende hinaus: dahinter liegen Reste anderer Module (wechseln je nach Ladereihenfolge
        # und geraeten sonst in die Signatur). Gezielte Spruenge in die gemeinsame Bibliothek (ab 0x2000) bleiben erlaubt.
        return bool(modlen) and a < modlen <= b
    ret = set()
    for p in pairs:
        c = lower_ctrl(p)
        if c and c[1] in ("BAL", "JALR"):
            ret.add((p.pc + 16) & 0x3FFF)
    seen = set()
    work = [x for x in seeds if ok(x)]
    retAdded = False
    while work:
        pc = work.pop()
        while ok(pc) and pc not in seen:
            seen.add(pc)
            p = bypc[pc]
            c = lower_ctrl(p)
            slot = (pc + 8) & 0x3FFF
            if p.eBit:
                if ok(slot): seen.add(slot)
                # Fortsetzung per MSCNT direkt hinter dem Verzoegerungspaar (Doppelpufferung: anhalten, weiter);
                # nur innerhalb der bekannten Modullaenge, sonst koennten Reste anderer Module in die Signatur geraten
                cont = (pc + 16) & 0x3FFF
                if modlen and cont < modlen:
                    work.append(cont)
                break
            if c:
                if ok(slot) and slot not in seen:
                    seen.add(slot)
                    sp = bypc[slot]
                    if sp.eBit or lower_ctrl(sp):
                        pass  # Sprung im Verzoegerungspaar: Spiel macht das nicht, Rest faengt der Wiedereinstieg ab
                after = (pc + 16) & 0x3FFF
                if c[0] == "static":
                    work.append(c[2])
                    if c[1] not in ("B",) and not crosses(pc, after):
                        work.append(after)  # bedingt: nicht genommen; BAL: Ruecksprung
                else:
                    if c[1] == "JALR":
                        work.append(after)
                    # Ziel dynamisch: Ruecksprungadressen der Aufrufe innerhalb des Moduls (nicht die aus Speicherresten
                    # oberhalb - die zogen sonst fremden Code in Uebersetzung und Signatur); ohne Modullaenge wie bisher
                    if not retAdded:
                        work.extend(x for x in ret if not modlen or x - 16 < modlen); retAdded = True
                break
            if crosses(pc, slot):
                break
            pc = slot
    # Vorsicht: nur behalten, was mit dem Mitschnitt verbunden ist (Seeds sind sicher ausgefuehrt worden)
    return seen

def lower_ctrl(d):
    """('static', name, target) / ('dyn', name, None) / None"""
    if d.iBit:
        return None
    L = d.lower
    if L >> 31:
        return None
    op = (L >> 25) & 0x7F
    imm = L & 0x7FF
    if imm & 0x400:
        imm -= 0x800
    if op in BR_STATIC:
        return ("static", BR_STATIC[op], (d.pc + 8 + imm * 8) & 0x3FFF)
    if op in BR_DYN:
        return ("dyn", BR_DYN[op], None)
    return None

def rbvi(reg):
    if reg == 0:
        return "0"
    return "((v.m_viBranchBackupValid && v.m_viBranchBackupReg == %d) ? v.m_viBranchBackupValue : v.m_state.vi[%d])" % (reg, reg)

def lower3_inline(d, w):
    """Lower-Befehl fuer Stufe 3. Gibt True zurueck, wenn er hier vollstaendig behandelt wurde."""
    L, pc = d.lower, d.pc
    op = (L >> 25) & 0x7F
    vit, vis = (L >> 16) & 0xF, (L >> 11) & 0xF
    ctrl = lower_ctrl(d)
    if ctrl and ctrl[0] == "static":
        _, name, tgt = ctrl
        cond = {"B": "true", "BAL": "true",
                "IBEQ": "(int16_t)%s == (int16_t)%s" % (rbvi(vis), rbvi(vit)),
                "IBNE": "(int16_t)%s != (int16_t)%s" % (rbvi(vis), rbvi(vit)),
                "IBLTZ": "(int16_t)%s < 0" % rbvi(vis), "IBGTZ": "(int16_t)%s > 0" % rbvi(vis),
                "IBLEZ": "(int16_t)%s <= 0" % rbvi(vis), "IBGEZ": "(int16_t)%s >= 0" % rbvi(vis)}[name]
        w("            brTake = %s;" % cond)
        if name == "BAL" and vit != 0:
            w("            v.m_state.vi[%d] = %d;" % (vit, (pc + 16) // 8))
        return True
    if ctrl and ctrl[0] == "dyn":
        _, name, _ = ctrl
        w("            brDyn = ((uint32_t)(uint16_t)%s * 8u) & 0x3FFFu;" % rbvi(vis))
        if name == "JALR" and vit != 0:
            w("            v.m_state.vi[%d] = %d;" % (vit, (pc + 16) // 8))
        return True
    if not (L >> 31) and op in FLAG_READERS:
        w("            fr.commitReady(v, v.m_cycle);")
        return False  # Interpreter-Befehl liest danach den eingetragenen Zustand
    if STUFE >= 5 and not (L >> 31) and op in (0x10, 0x12, 0x13, 0x1C):  # FCEQ FCAND FCOR FCGET
        w("            cr.commitReady(v, v.m_cycle);")
        return False
    if (L >> 31) and (L & 0x3F) >= 0x3C:
        sp = (L & 3) | ((L >> 4) & 0x7C)
        if sp == 0x6C:  # XGKICK sofort
            w("            v.xgkickNow(static_cast<uint32_t>(static_cast<uint16_t>(v.m_state.vi[%d])));" % vis)
            return True
    return False

BLOCK_NEED = {}   # pc -> Menge der Bereitschafts-Terme, die zur Laufzeit geprueft werden muessen (Stufe 4)
BLOCK_DEAD = {}   # pc -> Menge der ueberfluessigen Bereitschafts-Speicherungen (Stufe 4)

def pair_rw(d):
    """Lese-Terme (wie ready_expr) und Schreib-Schluessel mit Latenz (in der Reihenfolge von gen_mark)."""
    reads = []
    for u in (d.uu, d.lu):
        for reg, lanes in u.vfreads():
            for c in range(4):
                if lanes & LANE[c]:
                    reads.append("v.m_vfReady[%d][%d]" % (reg, c))
        bits = u.viRead & 0xFFFE
        for reg in range(16):
            if bits & (1 << reg):
                reads.append("v.m_viReady[%d]" % reg)
        for c in range(4):
            if u.accRead & LANE[c]:
                reads.append("v.m_accReady[%d]" % c)
    UW, UWL, LW, LWL = d.uu.wreg, d.uu.wlanes, d.lu.wreg, d.lu.wlanes
    hasUpperWrite = UW != 0
    hasLowerWrite = LW != 0 and d.suppressed != LW
    writes = []
    if hasLowerWrite:
        for c in range(4):
            if LWL & LANE[c]:
                writes.append(("v.m_vfReady[%d][%d]" % (LW, c), d.lu.vfLat or d.lu.lat))
    if hasUpperWrite:
        for c in range(4):
            if UWL & LANE[c]:
                writes.append(("v.m_vfReady[%d][%d]" % (UW, c), d.uu.vfLat or d.uu.lat))
    for reg in range(1, 16):
        if d.lu.viWrite & (1 << reg):
            writes.append(("v.m_viReady[%d]" % reg, d.lu.viLat or d.lu.lat))
    for c in range(4):
        if d.uu.accWrite & LANE[c]:
            writes.append(("v.m_accReady[%d]" % c, 1))
    return reads, writes

def analyse_blocks(pairs, entries, exits):
    """Stufe 4 (wie microVU): innerhalb eines geradlinigen Blocks ist bekannt, welche Register schon bereit sind.
    Jeder Takt eines Paars ist >= 1, also ist ein im Block geschriebenes Register nach Latenz-vielen Paaren sicher
    fertig; ein schon geprueftes bleibt fertig. Nur die erste Pruefung je Register und Block bleibt zur Laufzeit.
    Speicherungen, die vor dem naechsten Lesen ueberschrieben werden, entfallen; an jedem Ausgang ist alles gespeichert."""
    BLOCK_NEED.clear(); BLOCK_DEAD.clear(); MARK_USED.clear()
    state = {}; pend = {}; j = 0; lastpc = None
    infl = None
    def flush():
        pend.clear()
    for p in pairs:
        if p.pc in entries or lastpc is None or p.pc != ((lastpc + 8) & 0x3FFF):
            flush(); state.clear(); j = 0
            infl = INFLIGHT.get(p.pc) if (STATIK and STATIK2 and STUFE >= 5) else None
        reads, writes = pair_rw(p)
        need = set()
        for k in reads:
            st = state.get(k)
            if st is None:
                if infl is None or k in infl:
                    need.add(k)
            elif st != "ok":
                kw, L, pcw = st
                if j - kw < L:
                    need.add(k)
                    pend.pop(k, None)  # Speicherung wird gelesen -> bleibt
                    MARK_USED.add((pcw, k))
            state[k] = "ok"
        BLOCK_NEED[p.pc] = need
        BLOCK_DEAD.setdefault(p.pc, set())
        for k, L in writes:
            if k in pend:
                BLOCK_DEAD[pend[k]].add(k)  # frueher gespeichert, nie gelesen, jetzt ueberschrieben
            state[k] = (j, L, p.pc)
            pend[k] = p.pc
        if p.pc in exits:
            flush()  # moeglicher Ausgang: alles bisher Gespeicherte bleibt
        lastpc = p.pc
        j += 1

DEAD_FLAGS = set()
CUR_PC = -1
# Statische Taktplanung (wie microVU): Takt-Zaehler wird je geradlinigem Abschnitt gesammelt (PEND) und nur dort
# nachgefuehrt, wo er gebraucht wird; Bereitschafts-Marken nur fuer Schreibzugriffe, die eine Abschnittsgrenze
# ueberleben koennen oder im Abschnitt noch geprueft werden; Q wird an Lesern/DIV explizit sichtbar gemacht.
STATIK = os.environ.get("VU1REC_STATIK", "1") == "1"
PEND = [0]
DIST_EXIT = {}   # pc -> Paare bis zum naechsten moeglichen Ausgang des Abschnitts (0 = dieses Paar)
MARK_USED = set()  # (pc, schluessel): Marke wird von einer Laufzeitpruefung im selben Abschnitt gelesen
INFLIGHT = {}      # Einstieg -> Schluessel, die dort noch unterwegs sein koennen (Stufe 2); None = unbekannt (alle)
STATIK2 = os.environ.get("VU1REC_STATIK2", "1") == "1"
ENTRIES5 = set()
BACKUP_SETTERS = set()
START_PCS = {}  # hash -> Startadressen (vu1_index.txt)
SIGS = {}  # hash -> (Bereiche, FNV der Bytes darin)
ENTRY_MAPS = {}  # hash -> Einstiege (fuer den Wiedereinstieg aus dem Interpreter)

def needs_vf0_reset(d):
    """Muss nach dem Paar vf0/vi0 wiederhergestellt werden? Nur wenn es (moeglicherweise) dorthin schreibt."""
    if d.shadow:
        return True
    U, L = d.upper, d.lower
    dest, ft, fd, op = (U >> 21) & 0xF, (U >> 16) & 0x1F, (U >> 6) & 0x1F, U & 0x3F
    if op >= 0x3C:
        sp = (U & 3) | ((U >> 4) & 0x7C)
        if sp in (0x2F, 0x30, 0x1F) or sp < 0x10 or 0x18 <= sp <= 0x1C or sp == 0x1E or 0x20 <= sp <= 0x2E:
            utgt = None
        elif 0x10 <= sp <= 0x17 or sp == 0x1D:
            utgt = ft
        else:
            return True
    elif op <= 0x2F:
        utgt = fd
    else:
        return True
    if utgt == 0 and dest != 0:
        return True
    if d.iBit:
        return False
    if lower_ctrl(d):
        return False  # Spruenge schreiben nur vi[vit] != 0 (BAL/JALR)
    if L in (0x00000000, 0x8000033C):
        return False
    opHi = (L >> 25) & 0x7F
    ldest, lft = (L >> 21) & 0xF, (L >> 16) & 0x1F
    if opHi in (0x01, 0x05):  # SQ, ISW
        return False
    if opHi == 0x00:  # LQ
        return lft == 0 and ldest != 0
    if opHi in (0x04, 0x08, 0x09):  # ILW, IADDIU, ISUBIU: Ziel 0 wird uebersprungen
        return False
    if opHi == 0x40 and (L & 0x3F) < 0x3C and (L & 0x3F) in (0x30, 0x31, 0x32, 0x34, 0x35):
        return False  # IADD/ISUB/IADDI/IAND/IOR: Ziel 0 wird uebersprungen
    if opHi == 0x40 and (L & 0x3F) >= 0x3C:
        sp = (L & 3) | ((L >> 4) & 0x7C)
        if sp in (0x30, 0x31, 0x34):  # MOVE, MR32, LQI
            return lft == 0 and ldest != 0
        if sp in (0x35, 0x6C, 0x3B):  # SQI, XGKICK, WAITQ
            return False
    return True

def is_fmac5(p):
    r = upper_call5(p.upper) if not p.iBit else None
    return bool(r) and any(x.startswith("fmac5<") for x in r)

def flag_liveness(pairs, prev):
    """Wie microVU: Flags eines FMAC sind tot, wenn jeder Leser (FS*/FM*) auf jedem Weg erst nach einem spaeteren
    FMAC kommt, der bis dahin >= 4 Paare alt (sicher eingetragen) ist. Ausgang/Programmende: ein spaeterer FMAC genuegt
    (commitAll traegt alle ein). Dynamische Spruenge und Tiefe > 256: lebendig (vorsichtig)."""
    DEAD_FLAGS.clear()
    bypc = {p.pc: p for p in pairs}
    reader = {p.pc for p in pairs if not p.iBit and not (p.lower >> 31) and ((p.lower >> 25) & 0x7F) in FLAG_READERS}
    sup = {p.pc for p in pairs if is_fmac5(p)}
    def succ(pc):
        nxt = (pc + 8) & 0x3FFF
        k = prev.get(pc)
        if k is None:
            return [nxt]
        if k[0] == "static":
            p0 = bypc.get((pc - 8) & 0x3FFF)
            c = lower_ctrl(p0) if p0 else None
            if c and c[1] in ("B", "BAL"):
                return [k[1]]
            return [k[1], nxt]
        if k[0] == "dyn":
            return ["dyn"]
        return ["exit"]
    for k in sorted(sup):
        live = False
        stack = [(s_, None, 1) for s_ in succ(k)]
        seen = set()
        while stack and not live:
            pc, dm, depth = stack.pop()
            if depth > 256:
                live = True; break
            if pc == "exit":
                if dm is None: live = True
                continue
            if pc == "dyn":
                if dm is None or dm < 4: live = True
                continue
            if pc not in bypc:
                if dm is None: live = True
                continue
            key = (pc, None if dm is None else min(dm, 4))
            if key in seen:
                continue
            seen.add(key)
            if pc in reader and (dm is None or dm < 4):
                live = True; break
            ndm = 0 if pc in sup else dm
            if ndm is not None and ndm >= 4:
                continue
            for s_ in succ(pc):
                stack.append((s_, None if ndm is None else ndm + 1, depth + 1))
        if not live:
            DEAD_FLAGS.add(k)

def emit6(lines, w, indent):
    for line in lines:
        if "v.execLower(" in line or "v.execUpper(" in line:
            FALLBACK[0] = True
        w(indent + line)

TICK = "\0TICK"  # Platzhalter: hier vergeht ein Takt (statische Planung sammelt ihn)
NEED_CYC = re.compile(r"m_cycle|fmac5<\d+, \d+, \d+, 1, |clip5\(|commitReady\(|commitAll\(|handBack\(|queueQ\(|xgkickNow\(|budgetEnd|\breturn\b|goto dispatch3")
Q_USE = re.compile(r"v\.m_state\.q\)|fmac5<\d+, 1, |queueQ\(")

def flush_pair(lines, w):
    """Statische Taktplanung: TICK-Platzhalter sammeln, den Zaehler nur vor Zeilen nachfuehren, die ihn brauchen
    (Marken, Flag-Ring, Q, XGKICK, Ausgaenge, Spruenge zu Einstiegen)."""
    def sync():
        if PEND[0]:
            w("            v.m_cycle += %du;" % PEND[0]); PEND[0] = 0
    for l in lines:
        if l == TICK:
            PEND[0] += 1; continue
        t = l.strip()
        if NEED_CYC.search(l):
            sync()
        else:
            m = re.search(r"goto M_([0-9a-f]{4});", l)
            if m and int(m.group(1), 16) in ENTRIES5:
                sync()
        w(l)

def gen_pair3(d, w_out, covered, prev):
    global CUR_PC
    CUR_PC = d.pc
    P = d.pc
    statik = STATIK and STUFE >= 5
    lines = []
    w = lines.append if statik else w_out
    if statik and P in ENTRIES5 and PEND[0]:
        raise SystemExit("interner Fehler: Taktzaehler am Einstieg %04x nicht nachgefuehrt (%d)" % (P, PEND[0]))
    if STUFE >= 6 and P in ENTRIES5:
        SH.clear(); SHA[0] = False
    LAST_FMAC[0] = None; FALLBACK[0] = False
    w("        M_%04x: {" % P)
    if P in HEADS:
        w("            if (v.m_cycle >= budgetEnd || v.m_stopRequested) { v.m_state.pc = 0x%04xu; fr.commitAll(v); return 0; }" % P)
    if d.uu.reserved or d.lu.reserved:
        w("            v.m_state.pc = 0x%04xu; fr.commitAll(v); v.m_flagsLive = true; return 1;" % P)
        w("        }")
        if statik: flush_pair(lines, w_out)
        return
    calc = ready_expr(d)
    calc = [c for c in calc if "m_xgkick" not in c]  # XGKICK laeuft sofort komplett: keine Wartezeit
    if STUFE >= 4 and P in BLOCK_NEED:
        need = BLOCK_NEED[P]
        def keep(c):
            if c.startswith("if (v.m_vfReady") or c.startswith("if (v.m_viReady") or c.startswith("if (v.m_accReady"):
                return c[4:c.index(" >")] in need
            return True
        calc = [c for c in calc if keep(c)]
    if len(calc) > 2:
        w("            { %s" % " ".join(c for c in calc if not c.startswith("return")).replace("uint64_t r = v.m_cycle;", "uint64_t r = v.m_cycle;"))
        if statik:
            w("              if (r > v.m_cycle) v.m_cycle = r; }")
        else:
            w("              if (r > v.m_cycle) { v.m_cycle = r; if (v.m_cycle >= v.m_nextCommit) v.commitReadyPipelines(); } }")
    body_start = len(lines) if statik else None
    W = next((r for r in range(1, 16) if d.lu.viWrite & (1 << r)), 0)
    UW, UWL = d.uu.wreg, d.uu.wlanes
    LW, LWL = d.lu.wreg, d.lu.wlanes
    hasUpperWrite = UW != 0
    hasLowerWrite = LW != 0 and d.suppressed != LW
    if W:
        w("            const int32_t oldVi = v.m_state.vi[%d];" % W)
    U, L = d.upper, d.lower
    def emit_upper():
        ui = upper_call5(U) if STUFE >= 5 else None
        if ui is None:
            ui = upper_inline(U)
        lines = ui if ui is not None else ["v.execUpper(0x%08xu);" % U]
        if not any("v.execUpper(" in x for x in lines):
            lines = [x for x in lines if not x.startswith("v.m_currentUpperInstruction")]
        emit6(lines, w, "            ")
    if d.iBit:
        emit_upper()
        w("            { const uint32_t bits = 0x%08xu; float imm; std::memcpy(&imm, &bits, 4); v.m_state.i = vu1NormalizeOperandInline(imm); }" % L)
    elif d.shadow:
        S = d.shadow
        w("            { float oldVf[4], upperVf[4]; std::memcpy(oldVf, v.m_state.vf[%d], 16);" % S)
        emit_upper()
        w("              std::memcpy(upperVf, v.m_state.vf[%d], 16); std::memcpy(v.m_state.vf[%d], oldVf, 16);" % (S, S))
        if not lower3_inline(d, w):
            li = lower_inline(L, P)
            emit6(li if li is not None else ["v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu);" % (L, U)], w, "              ")
        w("              std::memcpy(v.m_state.vf[%d], upperVf, 16); }" % S)
    else:
        emit_upper()
        if not lower3_inline(d, w):
            li = lower_inline(L, P)
            emit6(li if li is not None else ["v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu);" % (L, U)], w, "            ")
    if STUFE >= 6:
        # Schatten nach diesem Paar: Rueckfall auf den Interpreter oder Doppelschreiber -> alles verwerfen;
        # sonst werden vom Nicht-FMAC-Oberbefehl und vom Unterbefehl beschriebene Register schmutzig.
        if FALLBACK[0] or d.shadow:
            SH.clear(); SHA[0] = False
        else:
            if LAST_FMAC[0] is None:
                if UW:
                    SH.pop(UW, None)
                if d.uu.accWrite:
                    SHA[0] = False
            if LW:
                SH.pop(LW, None)
    if STUFE < 5 or P in ENTRIES5 or ((P - 8) & 0x3FFF) in BACKUP_SETTERS:
        w("            v.m_viBranchBackupValid = false;")
    if STUFE >= 4 and BLOCK_DEAD.get(P):
        dead = BLOCK_DEAD[P]
        buf = []
        gen_mark(d, buf.append, W, hasUpperWrite, hasLowerWrite, UW, UWL, LW, LWL)
        for line in buf:
            t = line.strip()
            if t.startswith("v.m_") and " = v.m_cycle + " in t and t.split(" = ")[0] in dead:
                continue
            w(line)
    else:
        gen_mark(d, w, W, hasUpperWrite, hasLowerWrite, UW, UWL, LW, LWL)
    if STUFE < 5 or needs_vf0_reset(d):
        w("            v.m_state.vf[0][0] = 0.0f; v.m_state.vf[0][1] = 0.0f; v.m_state.vf[0][2] = 0.0f; v.m_state.vf[0][3] = 1.0f; v.m_state.vi[0] = 0;")
    if statik:
        # Q (DIV/RSQRT/SQRT-Ergebnis) wird im Interpreter von commitReadyPipelines vor dem Paar sichtbar; hier explizit
        # vor jedem Paar, das Q liest oder die Q-Pipeline neu belegt (nach dem Warten auf die belegte Einheit)
        if any(Q_USE.search(x) for x in lines[body_start:]):
            lines.insert(body_start, "            if (v.m_fdiv.valid && v.m_fdiv.readyCycle <= v.m_cycle) v.publishQ();")
        w(TICK)
    else:
        w("            ++v.m_cycle; if (v.m_cycle >= v.m_nextCommit) v.commitReadyPipelines();")
    # Kontrollfluss
    nxt = (P + 8) & 0x3FFF
    pc_ = prev.get(P)  # Vorgaengerpaar (Verzoegerungsplatz?)
    if pc_ is not None:
        kind = pc_[0]
        if kind == "static":
            tgt = pc_[1]
            if tgt in covered:
                w("            if (brTake) { brTake = false; goto M_%04x; }" % tgt)
            else:
                w("            if (brTake) { brTake = false; v.m_state.pc = 0x%04xu; fr.commitAll(v); v.m_flagsLive = true; return 1; }" % tgt)
        elif kind == "dyn":
            w("            v.m_state.pc = brDyn; goto dispatch3;")
        elif kind == "end":
            w("            v.m_state.pc = 0x%04xu; fr.commitAll(v); programEnded = true; return 0;" % nxt)
    if nxt in covered:
        w("            goto M_%04x;" % nxt)
    else:
        w("            v.m_state.pc = 0x%04xu; fr.commitAll(v); v.m_flagsLive = true; return 1;" % nxt)
    w("        }")
    if statik: flush_pair(lines, w_out)

def program3_ok(pairs):
    """Stufe 3 nur, wenn der Fluss statisch sauber ist (kein Sprung im Verzoegerungsplatz, Platz abgedeckt, keine D/T-Bits)."""
    cov = {p.pc for p in pairs}
    bypc = {p.pc: p for p in pairs}
    for p in pairs:
        if p.dBit or p.tBit:
            return False, "D/T-Bit"
        c = lower_ctrl(p)
        if c or p.eBit:
            slot = (p.pc + 8) & 0x3FFF
            if slot not in cov:
                return False, "Verzoegerungsplatz %04x fehlt" % slot
            q = bypc[slot]
            if lower_ctrl(q) or q.eBit:
                return False, "Sprung im Verzoegerungsplatz %04x" % slot
            if c and p.eBit:
                return False, "E-Bit mit Sprung"
    return True, ""

def gen_program3(h, pairs, out):
    global HEADS, FLAG_TARGET
    FLAG_TARGET = "ring"
    start = len(out)
    try:
        _gen_program3(h, pairs, out)
    finally:
        FLAG_TARGET = "pipe"
    if STUFE >= 5:
        for k in range(start, len(out)):
            if "fr.commitAll(v);" in out[k]:
                out[k] = out[k].replace("fr.commitAll(v);", "fr.commitAll(v); cr.handBack(v);")
        # Takt-Zaehler als lokale Variable (Register): Byte-Schreibzugriffe auf den VU-Speicher zwingen den Compiler
        # sonst, v.m_cycle nach jedem Speichern neu zu laden. Zurueckgeschrieben wird vor jedem Aufruf, der m_cycle
        # liest (commitReadyPipelines, queueQ, xgkickNow), und an jedem Ausgang.
        for k in range(start, len(out)):
            l = out[k]
            if "FlagRing fr;" in l:
                continue
            l = l.replace("v.m_cycle", "cyc")
            l = l.replace("v.commitReadyPipelines();", "{ v.m_cycle = cyc; v.commitReadyPipelines(); }")
            l = l.replace("v.queueQ(", "v.m_cycle = cyc; v.queueQ(")
            l = l.replace("v.xgkickNow(", "v.m_cycle = cyc; v.xgkickNow(")
            l = l.replace("fr.commitAll(v);", "v.m_cycle = cyc; fr.commitAll(v);")
            out[k] = l
        for k in range(start, len(out)):
            if out[k].strip() == "FlagRing fr;":
                out[k] = out[k] + "\n    uint64_t cyc = v.m_cycle;"
                break

def _gen_program3(h, pairs, out):
    global HEADS
    w = out.append
    if STUFE >= 5 and any(not (p.lower >> 31) and (p.lower >> 25) == 0x11 for p in pairs):
        raise SystemExit("Programm %s enthaelt FCSET: Clip-Ring (Stufe 5) waere falsch - erst queueFcset im Ring nachbilden" % h)
    cov = {p.pc for p in pairs}
    prev = {}
    HEADS = set()
    for p in pairs:
        c = lower_ctrl(p)
        slot = (p.pc + 8) & 0x3FFF
        if c and c[0] == "static":
            prev[slot] = ("static", c[2])
            HEADS.add(c[2])
        elif c and c[0] == "dyn":
            prev[slot] = ("dyn",)
        elif p.eBit:
            prev[slot] = ("end",)
    entries = set(HEADS) | {0} | {x for x in START_PCS.get(h, ()) if x in cov}
    exits = set()
    bypc = {p.pc: p for p in pairs}
    for p in pairs:
        c = lower_ctrl(p)
        slot = (p.pc + 8) & 0x3FFF
        if ((p.pc - 8) & 0x3FFF) not in cov:
            entries.add(p.pc)
        if c and c[1] in ("BAL", "JALR"):
            entries.add((p.pc + 16) & 0x3FFF)  # Ruecksprungadresse
        if (c and c[1] in ("B", "JR")) or p.eBit:
            entries.add((p.pc + 16) & 0x3FFF)  # nicht durch Weiterlaufen erreichbar
        if c or p.eBit:
            exits.add(slot)
        if p.uu.reserved or p.lu.reserved:
            exits.add(p.pc)
        if slot not in cov:
            exits.add(p.pc)
    if STUFE >= 5:
        analyse_dist(pairs, entries, exits)
        analyse_inflight(pairs, entries, exits, prev)
    if STUFE >= 4:
        analyse_blocks(pairs, entries, exits)
    if STUFE >= 5:
        analyse_recpend(pairs, entries, exits)
    ENTRIES5.clear(); ENTRIES5.update(entries)
    PEND[0] = 0
    ENTRY_MAPS[h] = sorted(e for e in entries if e in cov) if STUFE >= 4 else sorted(cov)
    BACKUP_SETTERS.clear()
    for p in pairs:
        Wp = next((r for r in range(1, 16) if p.lu.viWrite & (1 << r)), 0)
        li = None if (p.iBit or lower_ctrl(p)) else lower_inline(p.lower, p.pc)
        if (Wp and p.lu.delayBr) or (not p.iBit and not lower_ctrl(p) and li is None):
            BACKUP_SETTERS.add(p.pc)  # setzt (oder koennte setzen) die VI-Sicherung fuer den Sprung danach
    if STUFE >= 5:
        flag_liveness(pairs, prev)
        print("  %s: %d von %d FMAC-Flags tot" % (h, len(DEAD_FLAGS), sum(1 for p in pairs if is_fmac5(p))))
    else:
        DEAD_FLAGS.clear()
    w("    // Stufe %d" % (4 if STUFE >= 4 else 3))
    if STATIK and STUFE >= 5:
        # ohne Takt-Pruefung je Paar duerfen keine Interpreter-Warteschlangen (Flags, Speicherungen, ACC) offen sein
        w("    if (v.m_state.branchPending || v.m_state.ebit || v.m_state.haltAfterDelaySlot || v.hasPendingRegWrites() || v.hasPendingOther()) return 1;")
    else:
        w("    if (v.m_state.branchPending || v.m_state.ebit || v.m_state.haltAfterDelaySlot || v.hasPendingRegWrites()) return 1;")
    w("    FlagRing fr;")
    if STUFE >= 5:
        w("    ClipRing cr;")
    w("    bool brTake = false; uint32_t brDyn = 0; (void)brTake; (void)brDyn;")
    if STUFE >= 6:
        w("    __m128 sh[32]; __m128 sha; (void)sh; (void)sha;")
        w("    if ((_mm_getcsr() & 0x8040u) != 0x8040u) _mm_setcsr(_mm_getcsr() | 0x8040u); // Stufe 6 braucht FTZ+DAZ")
    w("dispatch3:")
    w("    switch (v.m_state.pc)")
    w("    {")
    for p in pairs:
        if STUFE < 4 or p.pc in entries:
            w("    case 0x%04x: goto M_%04x;" % (p.pc, p.pc))
    if STUFE >= 4:
        w("    default: { static uint32_t s_miss = 0; if ((s_miss++ & 0x3FFu) == 0u) std::fprintf(stderr, \"[VU1-Rec] %s Einstieg %%04x nicht vorgesehen (%%u)\\n\", v.m_state.pc, s_miss); }" % h)
        w("             fr.commitAll(v); v.m_flagsLive = true; return 1;")
    else:
        w("    default: fr.commitAll(v); v.m_flagsLive = true; return 1;")
    w("    }")
    w("    {")
    for p in pairs:
        gen_pair3(p, w, cov, prev)
    w("    }")
    w("    return 1;")

FLAGRING = r'''
// Flag-Ring fuer Stufe 3: FMAC-Flags werden wie im Interpreter 4 Takte nach Ausgabe sichtbar, aber erst beim
// Lesen (FS*/FM*), bei Ueberlauf oder am Ende in m_state eingetragen (statt jeden Takt die Pipeline zu pruefen).
struct FlagRing
{
    uint64_t cyc[8];
    uint32_t mac[8], st[8];
    uint32_t head = 0, n = 0;
    static void apply(VU1Interpreter &v, uint32_t mac, uint32_t st);
    inline void applyOne(VU1Interpreter &v) { apply(v, mac[head], st[head]); head = (head + 1) & 7u; --n; }
    inline void push(VU1Interpreter &v, uint64_t c, uint32_t m, uint32_t s)
    {
        if (n == 8u) applyOne(v);
        const uint32_t i = (head + n) & 7u;
        cyc[i] = c; mac[i] = m; st[i] = s; ++n;
    }
    inline void commitReady(VU1Interpreter &v, uint64_t now) { while (n && cyc[head] + 4u <= now) applyOne(v); }
    inline void commitAll(VU1Interpreter &v) { while (n) applyOne(v); }
};
'''

CLIPRING = r"""
// Clip-Ring (Stufe 5): CLIP-Ergebnisse werden wie im Interpreter 4 Takte nach Ausgabe sichtbar, aber erst beim Lesen
// (FCAND/FCOR/FCEQ/FCGET) eingetragen. An jedem Ausgang gehen offene Eintraege mit ihren echten Takten zurueck in die
// Interpreter-Pipeline (gleiche Sichtbarkeit, gleiches Programmende).
struct VU1Interpreter::Compiled::ClipRing
{
    uint64_t cyc[8];
    uint32_t val[8];
    uint32_t head = 0, n = 0;
    inline void push(VU1Interpreter &v, uint64_t c, uint32_t value)
    {
        if (n == 8u) { v.m_state.clip = val[head]; head = (head + 1) & 7u; --n; }
        const uint32_t i = (head + n) & 7u;
        cyc[i] = c; val[i] = value; ++n;
    }
    inline void commitReady(VU1Interpreter &v, uint64_t now)
    {
        while (n && cyc[head] + VU1Interpreter::kFmacLatency <= now) { v.m_state.clip = val[head]; head = (head + 1) & 7u; --n; }
    }
    inline void handBack(VU1Interpreter &v)
    {
        commitReady(v, v.m_cycle);
        while (n)
        {
            bool placed = false;
            for (auto &entry : v.m_flagPipeline)
            {
                if (entry.valid) continue;
                entry = {};
                entry.valid = true;
                v.m_occ.flag |= 1u << static_cast<uint32_t>(&entry - v.m_flagPipeline.data());
                entry.issueCycle = cyc[head];
                entry.readyCycle = cyc[head] + VU1Interpreter::kFmacLatency;
                v.noteReady(entry.readyCycle);
                entry.clip = val[head];
                entry.writesClip = true;
                placed = true;
                break;
            }
            if (!placed) v.m_state.clip = val[head];
            head = (head + 1) & 7u; --n;
        }
    }
};
"""

if __name__ == "__main__":
    main()

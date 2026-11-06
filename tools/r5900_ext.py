#!/usr/bin/env python3
"""R5900 (PS2 Emotion Engine) extensions for a Unicorn MIPS64 emulator.

Unicorn has no R5900 model. This module scans code ranges for R5900-only encodings,
replaces each one with a nop and emulates it in Python from a per-address code hook
(the hook runs before the nop, so branch delay slots keep working).

Exact: lq/sq, ei/di, 3-operand mult/multu, madd/maddu, the HI1/LO1 pipeline
(mult1/div1/mfhi1/...), common 128-bit MMI moves/logic (por, pand, pxor, pnor,
pcpyld, pcpyud, pcpyh, pextlw, pextuw, paddw, psubw, pmfhi, pmflo, plzcw),
VU0 transfers (lqc2/sqc2, qmfc2/qmtc2, cfc2/ctc2).
Approximate: VU0 macro arithmetic (vadd/vmul/...) is only counted, not computed.
"""
import collections, struct
from unicorn.mips_const import *

GPR = [UC_MIPS_REG_ZERO, UC_MIPS_REG_AT, UC_MIPS_REG_V0, UC_MIPS_REG_V1, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
       UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_T0, UC_MIPS_REG_T1, UC_MIPS_REG_T2, UC_MIPS_REG_T3,
       UC_MIPS_REG_T4, UC_MIPS_REG_T5, UC_MIPS_REG_T6, UC_MIPS_REG_T7, UC_MIPS_REG_S0, UC_MIPS_REG_S1,
       UC_MIPS_REG_S2, UC_MIPS_REG_S3, UC_MIPS_REG_S4, UC_MIPS_REG_S5, UC_MIPS_REG_S6, UC_MIPS_REG_S7,
       UC_MIPS_REG_T8, UC_MIPS_REG_T9, UC_MIPS_REG_K0, UC_MIPS_REG_K1, UC_MIPS_REG_GP, UC_MIPS_REG_SP,
       UC_MIPS_REG_FP, UC_MIPS_REG_RA]
M64 = (1 << 64) - 1
M32 = (1 << 32) - 1


def s32(v): v &= M32; return v - (1 << 32) if v & 0x80000000 else v
def sx64(v): return s32(v) & M64


class R5900:
    def __init__(self, uc, hook_at, phys=lambda a: a & 0x1FFFFFFF):
        self.uc, self.hook_at, self.phys = uc, hook_at, phys
        self.ops = {}                 # addr -> original word
        self.hi = [0] * 32            # upper 64 bits of the 128-bit GPRs
        self.hi1 = self.lo1 = 0
        self.vf = [[0.0, 0.0, 0.0, 1.0 if i == 0 else 0.0] for i in range(32)]
        self.vfraw = [bytearray(16) for _ in range(32)]
        self.vi = [0] * 32
        self.count = collections.Counter()

    # -- register helpers --------------------------------------------------
    def g(self, r): return 0 if r == 0 else self.uc.reg_read(GPR[r]) & M64
    def s(self, r, v, hi=None):
        if r == 0: return
        self.uc.reg_write(GPR[r], v & M64)
        if hi is not None: self.hi[r] = hi & M64
    def q(self, r): return (self.hi[r] << 64) | self.g(r) if r else 0
    def setq(self, r, v): self.s(r, v & M64, v >> 64)

    # -- scanning ----------------------------------------------------------
    def classify(self, w):
        op, f = w >> 26, w & 0x3F
        if op in (0x1E, 0x1F, 0x1C, 0x36, 0x3E): return True
        if op == 0x12: return True                                  # COP2 (VU0)
        if w in (0x42000038, 0x42000039): return True                # ei / di
        if op == 0 and f in (0x18, 0x19) and (w >> 11) & 31: return True   # mult rd
        return False

    def patch(self, base, size):
        d = bytes(self.uc.mem_read(base, size)); n = 0
        for i in range(0, size - 3, 4):
            w = struct.unpack_from('<I', d, i)[0]
            if self.classify(w):
                a = base + i; self.ops[a] = w
                self.uc.mem_write(a, b'\0\0\0\0'); self.hook_at(a); n += 1
        return n

    # -- execution ---------------------------------------------------------
    def step(self, addr):
        w = self.ops.get(addr)
        if w is None: return False
        op, rs, rt, rd, sa, f = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31, w & 0x3F
        imm = w & 0xFFFF; simm = imm - 0x10000 if imm & 0x8000 else imm
        mem_r = lambda a, n: bytes(self.uc.mem_read(self.phys(a), n))
        mem_w = lambda a, b: self.uc.mem_write(self.phys(a), b)
        if op == 0x1E:                                   # lq
            lo, hi = struct.unpack('<QQ', mem_r((self.g(rs) + simm) & ~0xF, 16)); self.s(rt, lo, hi)
        elif op == 0x1F:                                 # sq
            mem_w((self.g(rs) + simm) & ~0xF, struct.pack('<QQ', self.g(rt), self.hi[rt] if rt else 0))
        elif w in (0x42000038, 0x42000039):
            pass                                         # ei / di
        elif op == 0:                                    # mult/multu with rd
            a, b = self.g(rs), self.g(rt)
            p = s32(a) * s32(b) if f == 0x18 else (a & M32) * (b & M32)
            lo, hi = sx64(p), sx64(p >> 32)
            self.uc.reg_write(UC_MIPS_REG_LO, lo); self.uc.reg_write(UC_MIPS_REG_HI, hi); self.s(rd, lo)
        elif op == 0x1C:
            self.mmi(w, rs, rt, rd, sa, f)
        elif op == 0x36:                                 # lqc2
            self.vfraw[rt] = bytearray(mem_r((self.g(rs) + simm) & ~0xF, 16)); self.vf_from_raw(rt)
        elif op == 0x3E:                                 # sqc2
            mem_w((self.g(rs) + simm) & ~0xF, bytes(self.vfraw[rt]))
        elif op == 0x12:
            self.cop2(w, rs, rt, rd, f)
        return True

    def vf_from_raw(self, i):
        if i == 0: self.vfraw[0] = bytearray(struct.pack('<4f', 0, 0, 0, 1))
        self.vf[i] = list(struct.unpack('<4f', self.vfraw[i]))

    def cop2(self, w, rs, rt, rd, f):
        if rs == 1:      # qmfc2 rt, vf[rd]
            lo, hi = struct.unpack('<QQ', bytes(self.vfraw[rd])); self.s(rt, lo, hi); self.count['qmfc2'] += 1
        elif rs == 5:    # qmtc2
            if rd: self.vfraw[rd] = bytearray(struct.pack('<QQ', self.g(rt), self.hi[rt])); self.vf_from_raw(rd)
            self.count['qmtc2'] += 1
        elif rs == 2:    # cfc2
            self.s(rt, sx64(self.vi[rd] if rd < 32 else 0)); self.count['cfc2'] += 1
        elif rs == 6:    # ctc2
            if rd: self.vi[rd] = self.g(rt) & M32
            self.count['ctc2'] += 1
        else:
            self.count[f'vu0 macro {w:#010x}'[:18]] += 1

    def mmi(self, w, rs, rt, rd, sa, f):
        A, B = self.q(rs), self.q(rt)
        lanes = lambda v: [(v >> (32 * i)) & M32 for i in range(4)]
        pack = lambda l: sum((x & M32) << (32 * i) for i, x in enumerate(l))
        name = None
        if f == 0x00 or f == 0x01:                       # madd / maddu (rd)
            acc = ((self.uc.reg_read(UC_MIPS_REG_HI) & M32) << 32) | (self.uc.reg_read(UC_MIPS_REG_LO) & M32)
            p = s32(self.g(rs)) * s32(self.g(rt)) if f == 0 else (self.g(rs) & M32) * (self.g(rt) & M32)
            acc = (acc + p) & M64
            self.uc.reg_write(UC_MIPS_REG_LO, sx64(acc)); self.uc.reg_write(UC_MIPS_REG_HI, sx64(acc >> 32))
            self.s(rd, sx64(acc)); name = 'madd'
        elif f == 0x04:                                  # plzcw
            def lz(x):
                x &= M32; sign = x >> 31; n = 0
                for b in range(30, -1, -1):
                    if (x >> b) & 1 == sign: n += 1
                    else: break
                return n
            self.s(rd, lz(self.g(rs)) | (lz(self.g(rs) >> 32) << 32)); name = 'plzcw'
        elif f == 0x10: self.s(rd, self.hi1); name = 'mfhi1'
        elif f == 0x11: self.hi1 = self.g(rs); name = 'mthi1'
        elif f == 0x12: self.s(rd, self.lo1); name = 'mflo1'
        elif f == 0x13: self.lo1 = self.g(rs); name = 'mtlo1'
        elif f in (0x18, 0x19):                          # mult1 / multu1
            a, b = self.g(rs), self.g(rt)
            p = s32(a) * s32(b) if f == 0x18 else (a & M32) * (b & M32)
            self.lo1, self.hi1 = sx64(p), sx64(p >> 32); self.s(rd, self.lo1); name = 'mult1'
        elif f in (0x1A, 0x1B):                          # div1 / divu1
            a, b = (s32(self.g(rs)), s32(self.g(rt))) if f == 0x1A else (self.g(rs) & M32, self.g(rt) & M32)
            if b:
                qq = int(a / b) if f == 0x1A else a // b; r = a - qq * b
                self.lo1, self.hi1 = sx64(qq), sx64(r)
            name = 'div1'
        elif f == 0x08:                                  # MMI0
            la, lb = lanes(A), lanes(B)
            if sa == 0x00: self.setq(rd, pack([x + y for x, y in zip(la, lb)])); name = 'paddw'
            elif sa == 0x01: self.setq(rd, pack([x - y for x, y in zip(la, lb)])); name = 'psubw'
            elif sa == 0x12: self.setq(rd, pack([lb[0], la[0], lb[1], la[1]])); name = 'pextlw'
        elif f == 0x28:                                  # MMI1
            la, lb = lanes(A), lanes(B)
            if sa == 0x12: self.setq(rd, pack([lb[2], la[2], lb[3], la[3]])); name = 'pextuw'
        elif f == 0x09:                                  # MMI2
            if sa == 0x08: self.setq(rd, (self.hi1 << 64) | (self.uc.reg_read(UC_MIPS_REG_HI) & M64)); name = 'pmfhi'
            elif sa == 0x09: self.setq(rd, (self.lo1 << 64) | (self.uc.reg_read(UC_MIPS_REG_LO) & M64)); name = 'pmflo'
            elif sa == 0x0E: self.setq(rd, ((A & M64) << 64) | (B & M64)); name = 'pcpyld'
            elif sa == 0x12: self.setq(rd, A & B); name = 'pand'
            elif sa == 0x13: self.setq(rd, A ^ B); name = 'pxor'
        elif f == 0x29:                                  # MMI3
            if sa == 0x0E: self.setq(rd, (B & ~M64) | (A >> 64)); name = 'pcpyud'
            elif sa == 0x12: self.setq(rd, A | B); name = 'por'
            elif sa == 0x13: self.setq(rd, ~(A | B) & ((1 << 128) - 1)); name = 'pnor'
            elif sa == 0x1B:
                h0, h1 = B & 0xFFFF, (B >> 64) & 0xFFFF
                self.setq(rd, (h0 * 0x0001000100010001) | ((h1 * 0x0001000100010001) << 64)); name = 'pcpyh'
        self.count[name or f'MMI f={f:#x} sa={sa:#x} (nop)'] += 1

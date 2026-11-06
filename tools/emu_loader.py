#!/usr/bin/env python3
"""Run the real SotC loader code under Unicorn (MIPS64 LE) to learn where it places
KERNEL.XFF in memory, without a PS2 BIOS or emulator.

Starts at execProgWithThread("cdrom0:\\STARTUP.XFF;1") with the loader heap set up
exactly like the loader's main() does, emulates the loader's file/print/thread
helpers in Python (HLE) and stops right before it jumps into the loaded module.

Usage: emu_loader.py <game_dir> <sotc_linked.elf> <out.json> [--dump mem.bin]
"""
import collections, json, os, re, struct, sys
from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS64, UC_MODE_LITTLE_ENDIAN, \
    UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_BLOCK, UC_HOOK_MEM_UNMAPPED, UC_HOOK_INSN_INVALID
from unicorn.mips_const import *
from elftools.elf.elffile import ELFFile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r5900_ext import R5900

RAM = 32 * 1024 * 1024
GP = 0x0013E9F0                       # set by _start
HEAP_VAR = GP - 0x7F6C                # loader bump-heap pointer
HEAP_START = (0x00164FE8 + 0x40000 + 0xF) & ~0xF   # main(): align16(_end + stack)
REG = {n: getattr(sys.modules['unicorn.mips_const'], f'UC_MIPS_REG_{n.upper()}') for n in
       ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
        's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']}
REGN = list(REG)


class Emu:
    def __init__(self, game_dir, elf_path):
        self.game = game_dir
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS64 + UC_MODE_LITTLE_ENDIAN)
        # R5900 = MIPS III + movz/movn/pref + MMI; a MIPS64r2 model covers all but MMI/lq/sq
        self.uc.ctl_set_cpu_model(UC_CPU_MIPS64_MIPS64R2_GENERIC)
        # EE RAM is visible at 0x00000000 (cached), 0x20000000 (uncached), 0x30000000 (uncached accel.)
        import ctypes
        self._ram = ctypes.create_string_buffer(RAM)
        for base in (0x00000000, 0x20000000, 0x30000000):
            self.uc.mem_map_ptr(base, RAM, 7, ctypes.addressof(self._ram))
        self.uc.mem_map(0x70000000, 0x4000)          # scratchpad
        e = ELFFile(open(elf_path, 'rb'))
        for seg in e.iter_segments():
            if seg['p_type'] == 'PT_LOAD' and seg['p_vaddr'] < 0x200000:   # loader only
                self.uc.mem_write(seg['p_vaddr'], seg.data())
        self.sym = {s.name: s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols() if s.name}
        self.name = {v: k for k, v in self.sym.items()}
        self.files, self.next_fd = {}, 3
        class Log(list):
            def append(self_, x):
                super().append(x); print(x, flush=True)
        self.log, self.threads, self.stop_reason = Log(), [], None
        self.hle = {}
        for nm, fn in [('LoaderSysGetstat', self.getstat), ('LoaderSysFOpen', self.fopen),
                       ('LoaderSysFRead', self.fread), ('LoaderSysFClose', self.fclose),
                       ('LoaderSysPrintf', self.printf), ('PutString', self.putstring),
                       ('PutStringS', self.putstring), ('CreateThread', self.create_thread),
                       ('StartThread', self.start_thread), ('FlushCache', self.ret0),
                       ('memcpy', self.memcpy), ('memmove', self.memcpy), ('memset', self.memset),
                       ('strlen', self.strlen), ('strcpy', self.strcpy), ('strncpy', self.strncpy),
                       ('strcmp', self.strcmp), ('strncmp', self.strncmp), ('strcat', self.strcat),
                       ('memcmp', self.memcmp), ('sscanf', self.sscanf), ('sprintf', self.sprintf),
                       ('malloc', self.malloc), ('free', self.ret0),
                       ('printf', self.cprintf), ('scePrintf', self.cprintf), ('sceTtyWrite', self.ttywrite)]:
            self.hle[self.sym[nm]] = (nm, fn)
        self.uc.hook_add(UC_HOOK_INTR, self.on_intr)
        self.blocks = 0; self.hot = collections.Counter()
        self.sc_count = collections.Counter(); self.heap_end = 0x1A4FE8   # _start: SetupHeap(_end, 0x40000)
        self.cur_tid = 1; self.sema = 0; self.semas = {}; self.waits = []; self.sleeps = 0
        self.uc.hook_add(UC_HOOK_BLOCK, self.on_block)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self.on_unmapped)
        self.uc.hook_add(UC_HOOK_INSN_INVALID, self.on_invalid)
        self.stop_at = {}
        self.trace = []
        self.malloc_ptr = 0x01800000
        self.hi = {}
        self.hooked = set(); self.handles = {}
        self.r59 = R5900(self.uc, self.hook_at)
        self.r59.patch(0x100000, 0x2EAEC)                  # loader .text
        for a in list(self.hle): self.hook_at(a)
        self.hook_at(0x4)

    # --- helpers -----------------------------------------------------------
    def r(self, n): return self.uc.reg_read(REG[n]) & 0xFFFFFFFF
    def w(self, n, v): self.uc.reg_write(REG[n], v & 0xFFFFFFFFFFFFFFFF)
    def rd32(self, a): return struct.unpack('<I', self.uc.mem_read(a & 0x1FFFFFFF, 4))[0]
    def cstr(self, a):
        out = bytearray(); a &= 0x1FFFFFFF
        while True:
            b = self.uc.mem_read(a, 64); i = b.find(b'\0')
            if i >= 0: return (out + b[:i]).decode('latin1')
            out += b; a += 64
    def ret(self, v=0):
        v &= 0xFFFFFFFF
        self.w('v0', v - (1 << 32) if v & 0x80000000 else v)   # sign-extend like the EE does
        self.uc.reg_write(UC_MIPS_REG_PC, self.r('ra'))

    def host_path(self, p):
        p = re.sub(r'^[a-z0-9]+:', '', p).replace('\\', '/').lstrip('/')
        p = p.split(';')[0]
        return os.path.join(self.game, *p.split('/'))

    # --- HLE functions -----------------------------------------------------
    def getstat(self):
        path = self.host_path(self.cstr(self.r('a0'))); buf = self.r('a1')
        if not os.path.exists(path): self.log.append(f'getstat MISSING {path}'); return self.ret(-1)
        st = bytearray(0x40); struct.pack_into('<III', st, 0, 0x2124, 0, os.path.getsize(path))
        self.uc.mem_write(buf, bytes(st)); self.ret(0)
    def fopen(self):
        path = self.host_path(self.cstr(self.r('a0')))
        if not os.path.exists(path): self.log.append(f'fopen MISSING {path}'); return self.ret(-2)
        fd = self.next_fd; self.next_fd += 1; self.files[fd] = open(path, 'rb')
        self.log.append(f'fopen {os.path.basename(path)} -> {fd}'); self.ret(fd)
    def fread(self):
        fd, buf, n = self.r('a0'), self.r('a1'), self.r('a2')
        data = self.files[fd].read(n); self.uc.mem_write(buf, data)
        self.log.append(f'fread fd={fd} buf={buf:#x} n={n:#x} -> {len(data):#x}'); self.ret(len(data))
    def fclose(self):
        f = self.files.pop(self.r('a0'), None); f and f.close(); self.ret(0)
    def ret0(self): self.ret(0)

    def fmt(self, f, args):
        out, ai = '', iter(args)
        for m in re.finditer(r'%([-0-9.]*)(l*)([sdixXpuc%])|[^%]+|%', f):
            if m.group(3) is None:
                out += m.group(0); continue
            flags, conv = m.group(1), m.group(3)
            if conv == '%': out += '%'; continue
            v = next(ai, 0)
            if conv == 's': s = self.cstr(v) if v else '(null)'
            elif conv == 'p': s = f'{v:#010x}'
            elif conv in 'xX': s = format(v, ('0' if flags.startswith('0') else '') + flags.lstrip('0-') + conv)
            elif conv == 'c': s = chr(v & 0xFF)
            else: s = str(struct.unpack('<i', struct.pack('<I', v))[0])
            if conv in 'sd' and flags.lstrip('-'):
                wdt = int(flags.lstrip('-0').split('.')[0] or 0)
                s = s.ljust(wdt) if flags.startswith('-') else s.rjust(wdt)
            out += s
        return out
    def varargs(self, first):
        regs = ['a0', 'a1', 'a2', 'a3', 't0', 't1', 't2', 't3']
        return [self.r(x) for x in regs[regs.index(first):]]
    def printf(self):
        s = self.fmt(self.cstr(self.r('a0')), self.varargs('a1'))
        self.log.append(s.rstrip('\n')); self.ret(len(s))
    def cprintf(self):
        s_ = self.fmt(self.cstr(self.r('a0')), self.varargs('a1'))
        self.log.append(s_.rstrip('\n')); self.ret(len(s_))
    def ttywrite(self):
        n = self.r('a2'); self.log.append('tty: ' + self.mread(self.r('a1'), n).decode('latin1').rstrip()); self.ret(n)
    def putstring(self):
        s = self.fmt(self.cstr(self.r('a1')), self.varargs('a2'))
        self.log.append(s.rstrip('\n')); self.ret(0)
    def create_thread(self):
        p = self.r('a0'); func, stack, size = self.rd32(p + 4), self.rd32(p + 8), self.rd32(p + 12)
        self.threads.append({'func': func, 'stack': stack, 'stack_size': size, 'gp': self.rd32(p + 16)})
        self.log.append(f'CreateThread func={func:#x} stack={stack:#x} size={size:#x}')
        self.ret(len(self.threads))
    def start_thread(self):
        tid, arg = self.r('a0'), self.r('a1')
        self.threads[tid - 1]['arg'] = arg
        self.log.append(f'StartThread {tid} arg={arg:#x}'); self.ret(0)

    # --- libc HLE (the originals use R5900-only 128-bit lq/sq) -------------
    def mread(self, a, n): return bytes(self.uc.mem_read(a & 0x1FFFFFFF, n)) if n else b''
    def mwrite(self, a, b): b and self.uc.mem_write(a & 0x1FFFFFFF, bytes(b))
    def memcpy(self):
        d, s_, n = self.r('a0'), self.r('a1'), self.r('a2'); self.mwrite(d, self.mread(s_, n)); self.ret(d)
    def memset(self):
        d, c, n = self.r('a0'), self.r('a1') & 0xFF, self.r('a2'); self.mwrite(d, bytes([c]) * n); self.ret(d)
    def strlen(self): self.ret(len(self.cstr(self.r('a0')).encode('latin1')))
    def strcpy(self):
        d = self.r('a0'); self.mwrite(d, self.cstr(self.r('a1')).encode('latin1') + b'\0'); self.ret(d)
    def strncpy(self):
        d, n = self.r('a0'), self.r('a2'); b = self.cstr(self.r('a1')).encode('latin1')[:n]
        self.mwrite(d, b + b'\0' * (n - len(b))); self.ret(d)
    def strcat(self):
        d = self.r('a0'); cur = self.cstr(d).encode('latin1')
        self.mwrite(d + len(cur), self.cstr(self.r('a1')).encode('latin1') + b'\0'); self.ret(d)
    def _cmp(self, a, b): return (a > b) - (a < b)
    def strcmp(self): self.ret(self._cmp(self.cstr(self.r('a0')), self.cstr(self.r('a1'))) & 0xFFFFFFFF)
    def strncmp(self):
        n = self.r('a2'); self.ret(self._cmp(self.cstr(self.r('a0'))[:n], self.cstr(self.r('a1'))[:n]) & 0xFFFFFFFF)
    def memcmp(self):
        n = self.r('a2'); self.ret(self._cmp(self.mread(self.r('a0'), n), self.mread(self.r('a1'), n)) & 0xFFFFFFFF)
    def sprintf(self):
        s_ = self.fmt(self.cstr(self.r('a1')), self.varargs('a2'))
        self.mwrite(self.r('a0'), s_.encode('latin1') + b'\0'); self.ret(len(s_))
    def malloc(self):
        n = (self.r('a0') + 15) & ~15; a = self.malloc_ptr; self.malloc_ptr += n
        self.log.append(f'malloc {n:#x} -> {a:#x}'); self.ret(a)
    def sscanf(self):
        src, fmt_ = self.cstr(self.r('a0')), self.cstr(self.r('a1'))
        outs = self.varargs('a2'); oi = 0; i = 0; count = 0
        for m in re.finditer(r'%(\d*)([sdixuc])|\s+|[^%\s]+', fmt_):
            tok = m.group(0)
            if tok.isspace():
                while i < len(src) and src[i].isspace(): i += 1
                continue
            if not tok.startswith('%'):
                if not src.startswith(tok, i): break
                i += len(tok); continue
            width, conv = int(m.group(1) or 0), m.group(2)
            if conv != 'c':
                while i < len(src) and src[i].isspace(): i += 1
            if i >= len(src): break
            if conv == 's':
                j = i
                while j < len(src) and not src[j].isspace() and (not width or j - i < width): j += 1
                self.mwrite(outs[oi], src[i:j].encode('latin1') + b'\0'); i = j
            elif conv == 'c':
                self.mwrite(outs[oi], src[i].encode('latin1')); i += 1
            else:
                mm = re.match(r'[-+]?(0[xX][0-9a-fA-F]+|[0-9a-fA-F]+)' if conv == 'x' else
                              r'[-+]?(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)' if conv == 'i' else r'[-+]?[0-9]+', src[i:])
                if not mm: break
                v = int(mm.group(0), 16 if conv == 'x' else 0 if conv == 'i' else 10)
                self.mwrite(outs[oi], struct.pack('<I', v & 0xFFFFFFFF)); i += mm.end()
            oi += 1; count += 1
        self.log.append(f'sscanf({src!r}, {fmt_!r}) -> {count}'); self.ret(count)

    # --- hooks -------------------------------------------------------------
    def hook_at(self, a):
        if a not in self.hooked:
            self.hooked.add(a); self.handles[a] = self.uc.hook_add(UC_HOOK_CODE, self.on_code, begin=a, end=a)

    def unhook_range(self, base, size):
        n = 0
        for a in [x for x in self.handles if base <= x < base + size]:
            self.uc.hook_del(self.handles.pop(a)); self.hooked.discard(a); n += 1
        return n

    def patch_lqsq(self, base, size):
        """R5900 lq/sq: replace by nop and emulate (128-bit) in a hook. Code sections only!"""
        if not hasattr(self, 'lqsq'): self.lqsq = {}
        d = bytes(self.uc.mem_read(base, size)); n = 0
        for i in range(0, size, 4):
            w = struct.unpack_from('<I', d, i)[0]
            if w >> 26 in (0x1E, 0x1F):
                self.lqsq[base + i] = w; self.uc.mem_write(base + i, b'\0\0\0\0'); n += 1
                if hasattr(self, 'hooked'): self.hook_at(base + i)
        return n

    def do_lqsq(self, w):
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        imm = imm - 0x10000 if imm & 0x8000 else imm
        ea = ((self.uc.reg_read(REG[REGN[rs]]) + imm) & ~0xF) & 0x1FFFFFFF
        if op == 0x1F:
            lo = self.uc.reg_read(REG[REGN[rt]]) & (2 ** 64 - 1) if rt else 0
            self.uc.mem_write(ea, struct.pack('<QQ', lo, self.hi.get(rt, 0) if rt else 0))
        elif rt:
            lo, hi = struct.unpack('<QQ', self.uc.mem_read(ea, 16))
            self.uc.reg_write(REG[REGN[rt]], lo); self.hi[rt] = hi

    def patch_lqsq(self, base, size):   # kept for callers: now full R5900 patching
        return self.r59.patch(base, size)

    def on_code(self, uc, addr, size, _):
        self.r59.step(addr)
        self.trace.append(addr)
        if len(self.trace) > 64: del self.trace[0]
        if addr in self.stop_at:
            r = self.stop_at[addr]()
            if r is not None:
                self.stop_reason = r; uc.emu_stop(); return
        h = self.hle.get(addr)
        if h: h[1]()
    def syscall(self, n):
        a0, a1 = self.r('a0'), self.r('a1')
        self.sc_count[n] += 1
        if n == 0x3D:                       # SetupHeap(start, size)
            self.heap_end = a0 + a1 if a1 != 0xFFFFFFFF else 0x1FF0000; return a0
        if n == 0x3E: return self.heap_end  # EndOfHeap
        if n == 0x3C: return 0x1FF0000      # SetupThread -> stack top
        if n == 0x2F: return self.cur_tid   # GetThreadId
        if n == 0x40:                       # CreateSema
            self.sema += 1; init = self.rd32(a0 + 8)
            self.semas[self.sema] = init; return self.sema
        if n in (0x42, 0x43):               # (i)SignalSema
            self.semas[a0] = self.semas.get(a0, 0) + 1; return a0
        if n in (0x44, 0x45, 0x46):          # WaitSema / PollSema / iPollSema
            v = self.semas.get(a0, 0)
            if v > 0: self.semas[a0] = v - 1; return a0
            if n == 0x44: self.waits.append(a0)
            return a0 if n == 0x44 else -419 & 0xFFFFFFFF
        if n == 0x32: self.sleeps += 1; return 0     # SleepThread
        if n in (0x10, 0x11, 0x14, 0x15, 0x16, 0x17): return 1   # intc/dmac handlers
        return 0

    def on_block(self, uc, addr, size, _):
        self.blocks += 1
        if self.blocks % 200000 == 0:
            self.hot[self.func_of(addr)] += 1
            if self.blocks % 5000000 == 0:
                print(f'.. {self.blocks//1000000}M blocks, hot: {self.hot.most_common(4)}', flush=True)

    def func_of(self, addr):
        import bisect
        if not hasattr(self, '_fa'):
            self._fl = sorted((v, k) for k, v in self.sym.items() if 0x100000 <= v < RAM)
            self._fa = [x[0] for x in self._fl]
        k = bisect.bisect_right(self._fa, addr) - 1
        return f'{self._fl[k][1]}+{addr - self._fa[k]:#x}' if k >= 0 else hex(addr)

    def on_intr(self, uc, intno, _):
        pc = uc.reg_read(UC_MIPS_REG_PC)
        if intno == 17:   # syscall
            num = self.r('v1'); num = num - (1 << 32) if num & 0x80000000 else num
            r_ = self.syscall(abs(num)) & 0xFFFFFFFF
            self.w('v0', r_ - (1 << 32) if r_ & 0x80000000 else r_)
            uc.reg_write(UC_MIPS_REG_PC, pc + 4 if self.rd32(pc) == 0xC else pc)
        else:
            ra = self.r('ra'); ins = self.rd32(ra) if ra < RAM else 0
            self.log.append(f'exception {intno} at {pc:#x} ra={ra:#x} insn@ra={ins:#010x}; trace ' + ' '.join(f'{a:x}' for a in self.trace[-24:])); uc.emu_stop()
    def on_unmapped(self, uc, access, addr, size, value, _):
        base = addr & ~0xFFFFF
        self.log.append(f'map-on-demand {addr:#x}'); uc.mem_map(base, 0x100000); return True
    def on_invalid(self, uc):
        pc = uc.reg_read(UC_MIPS_REG_PC); ins = self.rd32(pc); op = ins >> 26
        rs, rt, imm = (ins >> 21) & 31, (ins >> 16) & 31, ins & 0xFFFF
        imm = imm - 0x10000 if imm & 0x8000 else imm
        if op in (0x1E, 0x1F):   # lq / sq (128-bit, R5900): emulate low 64 bits + zero/ignore high
            ea = (uc.reg_read(REG[REGN[rs]]) + imm) & ~0xF & 0x1FFFFFFF
            if op == 0x1F:
                uc.mem_write(ea, struct.pack('<Q', uc.reg_read(REG[REGN[rt]]) & (2**64 - 1)) + b'\0' * 8)
            elif rt:
                uc.reg_write(REG[REGN[rt]], struct.unpack('<Q', uc.mem_read(ea, 8))[0])
            uc.reg_write(UC_MIPS_REG_PC, pc + 4); return True
        self.log.append(f'invalid insn {ins:#010x} at {pc:#x}'); return False

    def call(self, fn, args, sp, until=None, max_ins=50_000_000):
        for r_, v in zip(['a0', 'a1', 'a2', 'a3'], args): self.w(r_, v)
        self.w('sp', sp); self.w('gp', GP); self.w('ra', 0x00000004)   # return -> sentinel
        self.stop_at.setdefault(0x4, lambda: 'returned')
        try:
            self.uc.emu_start(fn, 0xFFFFFFFF, count=max_ins)
        except UcError as ex:
            self.stop_reason = f'UcError {ex} at {self.uc.reg_read(UC_MIPS_REG_PC):#x}'
        return self.stop_reason


# Sony SDK / IOP side functions (in the loader) that only talk to the IOP or CD drive.
# They don't allocate EE memory from the game's heap, so stubbing them doesn't change the layout.
SDK_STUBS = {
    'sceSifInitRpc': 0, 'sceSifExitRpc': 0, 'sceSifCallRpc': 0, 'sceSifCheckStatRpc': 0,
    'sceSifInitIopHeap': 0, 'sceSifAllocIopHeap': 0x00100000, 'sceSifFreeIopHeap': 0,
    'sceSifLoadModule': 1, 'sceSifLoadModuleBuffer': 1, 'sceSifLoadStartModule': 1,
    'sceSifLoadStartModuleBuffer': 1, 'sceSifStopModule': 0, 'sceSifUnloadModule': 0,
    'sceSifSearchModuleByName': -1, 'sceSifSetDma': 1, 'sceSifDmaStat': -1, 'sceSifSetReg': 0,
    'sceSifGetReg': 0x10000, 'sceSifRebootIop': 1, 'sceSifSyncIop': 1, 'sceSifLoadFileReset': 0,
    'sceSifSetIopAddr': 0, 'sceSifGetIopAddr': 0,
    'sceCdInit': 1, 'sceCdMmode': 1, 'sceCdSync': 0, 'sceCdDiskReady': 2, 'sceCdGetError': 0,
    'sceCdStInit': 1, 'sceCdStStart': 1, 'sceCdStStop': 1, 'sceCdStRead': 0, 'sceCdStream': 1,
    'sceCdRead': 1, 'sceCdStatus': 0, 'sceCdBreak': 1, 'sceCdStandby': 1, 'sceCdTrayReq': 1,
    'sceSdRemoteInit': 0, 'sceSdRemote': 0, 'sceFsReset': 0, 'sceDmaReset': 0, 'sceDbcInit': 1,
    'sceMcInit': 0, 'sceMcSync': 1, 'sceMcGetInfo': 0, 'sceMcOpen': -1, 'sceMcGetDir': 0,
    'sceGsResetGraph': 0, 'sceGsSyncV': 0, 'sceGsSyncPath': 0, 'sceGsPutDispEnv': 0,
    'sceDevVif0Reset': 0, 'sceDevVu0Reset': 0, 'sceDevVif1Reset': 0, 'sceDevVu1Reset': 0,
    'sceDevGifReset': 0, 'sceDmaSync': 0, 'sceDmaSend': 0,
}


def read_placement(emu, hdr):
    rd = lambda a: emu.rd32(a)
    P = lambda p: p & 0x1FFFFFFF
    nsec, sectab, names, shstr = rd(hdr + 64), P(rd(hdr + 48)), P(rd(hdr + 68)), P(rd(hdr + 72))
    secs = {}
    for i in range(nsec):
        ptr, size, typ = rd(sectab + 0x20 * i + 4), rd(sectab + 0x20 * i + 8), rd(sectab + 0x20 * i + 16)
        nm = emu.cstr(shstr + rd(names + 4 * i))
        if nm in ('.text', '.data', '.rodata', '.bss', '.vutext', '.vudata', '.vubss'):
            secs[nm] = {'addr': hex(P(ptr)), 'size': hex(size), 'type': typ}
    return {'header': hex(hdr), 'sections': secs}


BOOT_THREAD = 0x1AE238   # thread created last by ioskernel (calls IosCdvdManagerSimulation)


def run_kernel(emu, entry, kernel_text):
    """Continue into ioskernel and run the boot thread until MANAGER/GAMECORE are loaded."""
    tb, ts = (int(x, 0) for x in kernel_text.split(':'))
    emu.patch_lqsq(tb, ts)
    for nm, rv in SDK_STUBS.items():
        a = emu.sym.get(nm)
        if a and a not in emu.hle:
            emu.hle[a] = (nm, (lambda v=rv, n=nm: (emu.log.append(f'stub {n}'), emu.ret(v & 0xFFFFFFFF))))
            emu.hook_at(a)
    sce_cd_search = emu.sym.get('sceCdSearchFile')
    if sce_cd_search:
        def cdsearch():
            f, name = emu.r('a0'), emu.cstr(emu.r('a1')); path = emu.host_path(name)
            ok = os.path.exists(path)
            if ok:
                emu.uc.mem_write(f & 0x1FFFFFFF, struct.pack('<II', 0x1000, os.path.getsize(path)))
            emu.log.append(f'sceCdSearchFile {name} -> {int(ok)}'); emu.ret(1 if ok else 0)
        emu.hle[sce_cd_search] = ('sceCdSearchFile', cdsearch); emu.hook_at(sce_cd_search)
    loaded = []
    placements = {}
    last_read = {}
    orig_fread = emu.fread
    def fread_watch():
        fd, buf = emu.r('a0'), emu.r('a1')
        f = emu.files.get(fd)
        if f is not None and f.tell() == 0:
            last_read['name'] = os.path.basename(f.name).split('.')[0]; last_read['buf'] = buf
        orig_fread()
    emu.hle[emu.sym['LoaderSysFRead']] = ('LoaderSysFRead', fread_watch)
    def at_module_jump():
        hdr = last_read.get('buf'); name = last_read.get('name')
        pl = read_placement(emu, hdr)
        pl['entry_called'] = hex(emu.r('s0'))
        placements[name] = pl
        emu.log.append(f'== {name} geladen: Header {hdr:#x}, Einsprung {emu.r("s0"):#x}, Sektionen ' +
                       ', '.join(f"{k}={v['addr']}" for k, v in pl['sections'].items()))
        if name == 'GAMECORE':
            return 'GAMECORE placed'
        txt = pl['sections'].get('.text')
        if txt:
            tb_, ts_ = int(txt['addr'], 16), int(txt['size'], 16)
            emu.r59.patch(tb_, ts_)
            ret_site = emu.uc.reg_read(UC_MIPS_REG_PC) + 8       # instruction after jalr + delay slot
            def module_returned(tb_=tb_, ts_=ts_, nm=name, site=ret_site):
                emu.stop_at.pop(site, None)            # one-shot
                n = emu.unhook_range(tb_, ts_)
                emu.log.append(f'== {nm} Einsprung zurueckgekehrt, {n} Hooks entfernt'); return 'resume'
            emu.stop_at[ret_site] = module_returned; emu.hook_at(ret_site)
        return None
    for a in (0x1B089C, 0x1B0964, 0x1B0A38):
        emu.stop_at[a] = at_module_jump; emu.hook_at(a)
    orig_fopen = emu.fopen
    def fopen_watch():
        nm = emu.cstr(emu.r('a0'))
        loaded.append(nm); orig_fopen()
    emu.hle[emu.sym['LoaderSysFOpen']] = ('LoaderSysFOpen', fopen_watch)
    n0 = len(emu.threads)
    emu.stop_reason = None
    r = emu.call(entry['entry'], [1, entry['arg_a1']], 0x1FE0000, max_ins=60_000_000)
    out = {'ioskernel': r, 'new_threads': [{k: hex(v) for k, v in t.items()} for t in emu.threads[n0:]]}
    # run the boot thread(s) it started, in order
    boot = [t for t in emu.threads[n0:] if t['func'] == BOOT_THREAD]
    for t in boot or emu.threads[n0:][::-1]:
        if 'arg' not in t: continue
        emu.stop_reason = None
        rr = emu.call(t['func'], [t['arg']], (t['stack'] + t['stack_size']) & ~0xF, max_ins=150_000_000)
        while rr == 'resume':          # paused so Unicorn really drops deleted hooks; continue in place
            emu.stop_reason = None
            try:
                emu.uc.emu_start(emu.uc.reg_read(UC_MIPS_REG_PC), 0xFFFFFFFF, count=150_000_000)
            except UcError as ex:
                emu.stop_reason = f'UcError {ex} at {emu.uc.reg_read(UC_MIPS_REG_PC):#x}'
            rr = emu.stop_reason
        out.setdefault('threads_run', []).append({'func': hex(t['func']), 'result': rr,
                                                  'pc': hex(emu.uc.reg_read(UC_MIPS_REG_PC))})
        if 'GAMECORE' in placements: break
    out['files_opened'] = loaded
    out['placements'] = placements
    out['syscalls'] = {hex(k): v for k, v in emu.sc_count.most_common()}
    out['r5900_ops_executed'] = dict(emu.r59.count.most_common(40))
    return out


def main():
    game, elf, out = sys.argv[1], sys.argv[2], sys.argv[3]
    emu = Emu(game, elf)
    emu.uc.mem_write(HEAP_VAR, struct.pack('<I', HEAP_START))
    path_addr = 0x1F00000
    emu.uc.mem_write(path_addr, b'cdrom0:\\STARTUP.XFF;1\0')
    # 1) execProgWithThread(path, 0): reads STARTUP.XFF, allocates the thread stack, starts the thread
    r1 = emu.call(emu.sym['execProgWithThread'], [path_addr, 0], 0x1FF0000)
    print("step1:", r1); print("\n".join(emu.log[-40:]))
    t = emu.threads[0]
    # 2) run the thread body (loads the module, then jumps to its entry via jalr $s1)
    entry = {}
    def at_jump():
        entry['entry'] = emu.r('s1'); entry['arg_a0'] = emu.r('a0'); entry['arg_a1'] = emu.r('s0')
        return 'reached module entry jump'
    emu.stop_at[0x101D54] = at_jump; emu.hook_at(0x101D54)
    emu.stop_reason = None
    r2 = emu.call(t['func'], [t.get('arg', 0)], (t['stack'] + t['stack_size']) & ~0xF)
    heap_after = emu.rd32(HEAP_VAR)
    stage3 = None
    if '--kernel' in sys.argv:
        stage3 = run_kernel(emu, entry, sys.argv[sys.argv.index('--kernel') + 1])
    res = {'stage3': stage3, 'heap_start': hex(HEAP_START), 'heap_after': hex(heap_after), 'step1': r1, 'step2': r2,
           'thread': {k: hex(v) for k, v in t.items()},
           'module_entry': hex(entry.get('entry', 0)), 'log': emu.log}
    json.dump(res, open(out, 'w'), indent=1)
    if '--dump' in sys.argv:
        open(sys.argv[sys.argv.index('--dump') + 1], 'wb').write(bytes(emu.uc.mem_read(0, RAM)))
    print(json.dumps({k: v for k, v in res.items() if k != 'log'}, indent=1))
    print('\n'.join(emu.log[-60:]))


if __name__ == '__main__':
    main()

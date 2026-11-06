<sub>[← back to README](../README.md)</sub>

# Correctness: findings and fixes

## Why this port is different

Most PS2 "recomp" projects stop at "it boots". This one was built as a **remaster foundation**: every
emulation detail that influences game behaviour was measured against a reference (PCSX2 and public
hardware documentation) and fixed at the root — in the recompiler, not with per-game hacks.

| Area | What was done |
|---|---|
| **Recompiler correctness** | Audited every FPU, VU0-macro and MMI instruction the game actually uses (instruction census over the whole linked ELF) against PCSX2 and hardware test vectors. Found and fixed **10 translation bugs** in the upstream recompiler (see [Findings](#fixes-in-other-projects)). |
| **Bit-exact physics** | Player position, camera position and camera basis match PCSX2 to the last printed digit on the same input recording (deterministic replay system with RAM snapshots at the pad poll). |
| **Deterministic** | Three identical runs under full machine load produce bit-identical frames, with the VU1 and GS running on their own threads. |
| **Native frame rate** | The game has no internal frame cap (`IVSync = 0`). The port renders one frame per VBlank: 50 FPS in PAL mode, 60 FPS when 60 Hz is selected in the game's own options menu. Physics stays correct because the game scales its time step by video mode itself. |
| **VU1 ahead-of-time compiler** | VU1 micro programs are dumped from the game, statically analysed (flag liveness, register readiness, branch delay quirks) and compiled to specialised C++/SSE. Equivalence to the cycle-accurate interpreter is checked with a built-in differential tester. |
| **Multithreaded** | VIF1/VU1 work runs on a worker thread (like PCSX2's MTVU), the GS on its own thread. |
| **HLE kernel without side effects** | The high-level kernel (threads, semaphores, SIF/RPC, memory card, CD) keeps all of its own state outside game RAM — an earlier layout clobbered the game heap and broke "Quit game". |
| **Real IOP** | IRX modules are executed by an IOP emulator with its own RAM (sound driver, CD streaming), with HLE only where a module talks to real hardware. |
| **HD text without AI** | Menu and subtitle textures are re-typeset: text lines are detected in the original textures, read with OCR, corrected, and re-rendered with free fonts at 4× resolution in the original layout. No AI upscaling ("AI slop") anywhere. See the [before/after](../README.md#hd-text--re-typeset-not-ai-upscaled) in the README. |
| **Input recording / replay** | Every bug report becomes a replay. The test harness runs dozens of replays in parallel, compares screenshots pixel-by-pixel across builds, and dumps RAM at chosen frames for comparison with PCSX2. |

### Correct vs. "looks right"

Some of the things that were found by comparing against the reference instead of eyeballing:

- `SQRT.S` was translated with the wrong source register (`fs` instead of `ft`). 67 of 137 sites computed
  the wrong root. Visible effect: the player could not walk — the analogue stick strength was wrong.
- `MOVZ`/`MOVN` wrote the full 128-bit register; the hardware writes only the lower 64 bits (1462 sites).
- `VDIV` by zero returned ±0 instead of ±MAX; `VMAX`/`VMINI` used host float compares instead of the
  integer compare the VU does (±0 and huge values differ).
- `PMULTW`, `PMTHI`, `PMTLO`, `PMFHL` were translated with 32-bit semantics — the colour/brightness
  math (`iosMulMvec`) was wrong.
- `SQRT.S` must round to nearest (like PCSX2's `recSQRT_S`) while every other FPU op truncates; 1 ulp
  difference flipped the exposure to white and made hair, reins and the player's movement drift.
- `rand()`, `sinf()`/`cosf()` and friends were replaced by bit-exact re-implementations of the PS2
  newlib routines (`k_sinf.c`, `k_cosf.c`, `e_rem_pio2f.c`, the 64-bit LCG) so random dust emitters,
  wind and animation match the console.
- The VU0 random-number instructions (`VRNEXT`/`VRINIT`/`VRXOR`) were not implemented with the hardware
  LFSR; `iosGetRandom` returned 0 or garbage, dust emitters never ended, and the heap leaked ~5 KB per
  puff until the game ran out of memory after ~8,700 frames.
- The H-blank timer ran at NTSC speed on a PAL game; the PAL rate is 15625 Hz.
- Interrupt handler stacks were placed on the game's main stack; HLE pools lived in game RAM.
## Fixes in other projects

Everything below was found while porting this game and verified against PCSX2 source / hardware docs.
The fixes live in this repository (`patches/`, `recomp/`) and are offered upstream where applicable.

**PS2Recomp (static recompiler)**
- `COP1 SQRT.S` reads `ft`, not `fs`
- `MOVZ`/`MOVN` write 64 bits, upper half preserved
- `PMULTW`: two signed 32×32→64 products into LO/HI and LO1/HI1 (not four unsigned ones summed)
- `PMTHI`/`PMTLO`/`PMFHL.LW/UW` full 128-bit semantics
- `VDIV`: ±MAX on division by zero, clamp on overflow; `VSQRT`/`VRSQRT` hardware special cases
- `VMAX`/`VMINI`: integer compare semantics
- `PADDUH`/`PSUBUH`: saturating
- `SQRT.S` round-to-nearest, all other FPU ops truncate (and `DIV.S` to nearest)
- VU0 macro `VRNEXT`/`VRINIT`/`VRXOR` with the hardware LFSR (patch in `patches/`)
- HLE kernel: callback/interrupt stacks and RPC/TLS pools must not live in game RAM
- Self-modifying `jal 0` in the game's loader (patched at runtime by the game itself) must be read
  from RAM instead of being baked in by the recompiler

**PCSX2 (used as the reference, and as the GS/SPU2 library)**
- A small in-process bridge (`gsbridge/`) that feeds GIF/VIF data into PCSX2's GS and SPU2 without
  running any of its CPU emulation (patch against a pinned PCSX2 commit).
## Third-party tools adapted or fixed

- **PS2Recomp** — forked; recompiler fixes listed above, runtime extended (IOP emulator integration,
  kernel pools out of game RAM, interrupt stacks, timing, PGO build, MTVU).
- **PCSX2** — not modified in behaviour, but wrapped: GS/SPU2 as an in-process library with a live
  GIF feed, settings overlay, texture replacement folder; used unchanged as the reference emulator.
- **sotc-decomp** — its `xff2.py` converts Team ICO's XFF containers to ELF objects; used as-is.
- **Tesseract OCR** (`deu`+`eng`) and **TeX Gyre** fonts — used by the HD-text tool.


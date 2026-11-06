# Architecture

## The game's structure (why this port is unusual)

`SYSTEM.CNF` boots `SCES_533.26`, which is only the **loader** plus Sony SDK libraries. `STARTUP.XFF`
tells the loader to load `KERNEL.XFF`; the actual game lives in three **XFF modules** — Team ICO's own
container around relocatable ELF objects — `KERNEL`, `MANAGER` and `GAMECORE`. The modules are not
stripped, so almost every function keeps its original name (`gamecore`, `ioskernel`, `updateCamera…`).
`NICO.DAT` (1 GB) is the resource archive; `XAB`–`XAE` are audio/FMV streams.

Instead of emulating the loader's runtime relocation, `tools/link_sotc.py` links loader and modules
**statically at fixed addresses** into one ELF. That is what the recompiler sees. Consequences:

- every function has a stable address → the generated code can call directly;
- the memory layout differs from a real console (PCSX2 places `MANAGER` 0xFA380 higher); the
  comparison tools account for that;
- the PAL disc differs from NTSC-U (different module layout, no 1:1 symbol match with sotc-decomp).

## Pipeline

1. `tools/extract_iso.py` – files from the ISO; `scan_archive_xff.py` – index of the archive.
2. `tools/link_sotc.py` – static link, `placement.json` (section placement), `code_pointers.txt`
   (function pointers found in relocations), dynamic call sites.
3. `ps2_analyzer` – config (`sotc_config.toml`): functions, stubs, entry points.
   `tools/filter_stubs.py` keeps only stubs the runtime really implements.
4. `ps2_recomp` – one C++ file per function. Two passes (`tools/find_missing_targets.py` adds jump
   targets the first pass missed).
5. `tools/vu1dump.py` – VU1 micro programs as the game uploads them; `tools/vu1rec.py` – the VU1
   ahead-of-time compiler (see below).
6. `tools/build_game.sh` / `tools/pc_bauen_pgo.sh` – build `ps2EntryRunner` (CMake/Ninja, GCC, PGO).

Generated files (steps 4 and 5) are derived from the game and are **never** committed.

## Runtime (`recomp/ps2xRuntime`)

- **EE**: generated code runs as ordinary C++ on one host thread; guest threads are coroutines
  scheduled by `EeScheduler` (semaphores, alarms, VBlank/HBlank, EE timers, interrupt handlers on
  their own stacks in kernel RAM).
- **Float semantics**: the host thread runs with MXCSR round-toward-zero + FTZ/DAZ like the EE FPU;
  `DIV.S` and `SQRT.S` round to nearest (as PCSX2); values with exponent 255 are treated as the PS2's
  large finite numbers, never as Inf/NaN. VU math uses the same conventions plus VU-specific clamping.
- **VU0 macro mode**: translated inline (incl. the Q/P pipelines, `VCLIP`, the `R` LFSR).
- **VU1**: either the cycle-accurate interpreter or the compiled programs (`PS2X_VU1_REC=1`), on a worker
  thread (`PS2X_MTVU=1`). `PS2X_VU1_DIFF=1` runs both and compares state after every program.
- **VIF/GIF/DMA**: VIF1 unpacking, GIF path arbitration (PATH1/2/3), DMA chains with the game's
  "kick first, fill later" pattern; the GS receives packets through the bridge.
- **GS/SPU2**: `libps2xgs.so` — PCSX2's GS (Vulkan, internal upscaling, texture replacements,
  deinterlacing off since the game draws full frames) and SPU2, driven by the bridge; no PCSX2 CPU
  emulation is involved.
- **IOP**: `ps2xIOP` executes the game's IRX modules (sound, CD streaming) with its own RAM and timers;
  SIF RPC between EE and IOP is emulated, with HLE providers for modules that touch hardware.
- **Kernel HLE**: syscalls, memory card (file-backed `.ps2` card), CD (sector reads from the ISO with
  the real LSNs), DECI2 console, system language from `LANG`.
- **Game glue** (`sotc_override.cpp`): CD file table across `NICO.DAT` + `XAB..XAE`, diagnostics
  (hardware watchpoints, call ring, profiler), input recording/replay, screenshots, RAM dumps.

## VU1 ahead-of-time compiler (`tools/vu1rec.py`)

Stages, each verified against the interpreter with the differential tester:

1. per-instruction translation with full pipeline bookkeeping;
2. static branch-delay analysis (VI writes in delay slots, the "branch reads old VI" quirk);
3. flag ring: FMAC flags become visible 4 cycles later but are only materialised when read;
4. readiness elimination: per basic block, only registers that are read before being written again
   keep their ready-cycle stamps;
5. shared SSE helpers (all four lanes at once, result masked to the dest lanes), static flag liveness
   (flags nobody reads are not computed), `vf0` reset elision.

Stage 6 (planned): fully static stall scheduling so that cycle counting leaves the hot path.

## Timing

VBlank is driven by wall-clock time (pacing) or, in tests, by estimated EE cycles (`PS2X_NO_PACING`).
The game runs one frame per VBlank (`IVSync = 0`) and scales its own time step by video mode, so
`PS2X_VBLANK_AUTO=1` simply follows the game's `SetGsCrt` (50 Hz PAL / 60 Hz NTSC).

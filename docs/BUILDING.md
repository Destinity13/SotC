# Building

Tested on CachyOS (Arch-based). Any recent Linux with GCC 14+, CMake, Ninja, Python 3.11+, Vulkan
drivers and the PCSX2 build dependencies should work.

## 0. What you need

- Your own dump of *Shadow of the Colossus* PAL (`SCES-53326`) as an ISO. Other regions are not
  supported (different module layout).
- No BIOS is required: the kernel is high-level emulated and the IOP modules come from the disc.
- ~10 GB of disk space during generation (the generated C++ is large).

## 1. Dependencies

```sh
tools/setup_cachyos.sh
```

## 2. GS/SPU2 library (once)

```sh
git clone https://github.com/PCSX2/pcsx2 gsb/pcsx2
cd gsb/pcsx2 && git checkout $(cat ../../gsbridge/PCSX2_COMMIT)
git apply ../../gsbridge/pcsx2_ps2x.patch
cp -r ../../gsbridge/ps2x-gsbridge . && cp ../../gsbridge/ps2x_deps.sh .github/workflows/scripts/linux/
./.github/workflows/scripts/linux/ps2x_deps.sh         # builds PCSX2's pinned 3rd-party deps
cmake -B ../bau_pcsx2 -G Ninja -DCMAKE_BUILD_TYPE=Release -DPS2X_GSBRIDGE=ON
cmake --build ../bau_pcsx2 --target ps2xgs             # → gsb/bau_pcsx2/bin/libps2xgs.so
```

## 3. Generate the game code from your ISO

```sh
SOTC_ISO=/path/to/your/dump.iso tools/pipeline.sh
```

This extracts the modules, links them (`elf/sotc_linked.elf`), runs the analyzer and the recompiler
twice (second pass with the jump targets found by the first), and writes the generated C++ to
`recomp/output/`. `tools/build_game.sh` then copies it into `recomp/ps2xRuntime/src/runner/`
(git-ignored).

VU1 programs: play once with `PS2X_VU1_DUMP=1` (or use the interpreter, slower) and run
`tools/vu1rec.py` to produce `recomp/ps2xRuntime/src/lib/vu/ps2_vu1_compiled.cpp` (git-ignored).

## 4. Build the runtime

```sh
SOTC_PGO=gen tools/pc_bauen_pgo.sh pc1   # instrumented build
tools/run_game.sh                        # play for a few minutes (profile is written on exit)
SOTC_PGO=use tools/pc_bauen_pgo.sh pc2   # optimised build
```

`SOTC_BAU_JOBS` sets the parallelism. Builds land in `build/sotc/ps2EntryRunner_<name>`.

## 5. Play

```sh
tools/run_game.sh
```

Settings live in `~/.config/sotc-recomp/einstellungen.ini`; the memory card in `build/Mcd001.ps2`
(a dated backup is made at every start). Useful environment variables are documented at the top of
`tools/run_game.sh` and in `recomp/ps2xRuntime/README.md`.

## Tests

- Unit tests: configure with `-DPS2X_BUILD_TEST=ON -DPS2X_BUILD_ANALYZER=ON` (without LTO/LLD),
  target `ps2x_tests`.
- Replays and screenshot regressions: `tools/sotc_worker.sh` (job queue) and `tools/sotc_bench.sh`.
- PCSX2 reference runs: `tools/pcsx2_nachspiel.py` (needs a PCSX2 AppImage and your own BIOS for PCSX2;
  the port itself needs none).

# Verification method

"Looks right" is not a test. Everything that changes game behaviour in this port was checked one of
these ways.

## 1. Input recording and replay

`SOTC_INPUT_RECORD=<file>` writes every pad poll (game frame counter, buttons, sticks) while playing;
`SOTC_INPUT_REPLAY=<file>` plays it back frame-exactly. Together with a memory-card template
(`SOTC_MEMCARD_TEMPLATE`) a replay reproduces a session bit for bit, which turns every bug report into a
regression test.

## 2. Screenshot regression across builds

The job queue (`tools/sotc_worker.sh`, `tools/sotc_bench.sh`) runs replays headless (offscreen Vulkan,
no window), takes screenshots at fixed frame numbers and compares them pixel by pixel between builds.
A correctness fix that is supposed to be invisible must produce identical frames; a fix that is supposed
to change something must change exactly the expected frames.

## 3. RAM snapshots vs. PCSX2

`SOTC_RAMDUMP_GC=<frames>` dumps the full 32 MB of EE RAM at the pad poll of chosen frames.
`tools/pcsx2_nachspiel.py` drives PCSX2 with the same recording through a virtual game pad (uinput)
and the PINE socket, slows emulation (`NominalScalar = 0.2`) so no input is dropped, synchronises on
the first physics step, and saves savestates at the same frames. Analysis tools (`spieler_vgl.py`,
`kam_diff.py`, `obj_felder.py`, `ramvgl.py`, …) compare objects field by field, taking the module
placement offset into account.

Result on the intro route: player position and camera position/basis match to the printed precision
for several hundred frames; residual differences come from PCSX2's load stalls between cutscene
segments, not from math.

## 4. Instruction audit

A census of every opcode in the linked ELF (MMI, COP1, COP2 macro, special, regimm) against the
translator, cross-checked with PCSX2's interpreter/recompiler sources and hardware test vectors
(ps2autotests). Dedicated small probes (`regcheck.py`, `vucheck.py`, `qcheck.py`) grep the generated
code for suspicious register usage patterns.

## 5. Differential VU1 testing

`PS2X_VU1_DIFF=1` runs each VU1 program through both the interpreter and the compiled version and
compares the full VU state (VF, VI, ACC, Q, P, flags, clip) afterwards.

## 6. Determinism

Three runs of the same replay with MTVU and the GS thread enabled, under full machine load (28
parallel jobs), produced bit-identical frames.

## 7. Unit tests

`ps2x_tests` (435/446 pass; the remaining failures are test-bed assumptions about an old CPU GS path,
IOP heap addresses and a VIF1 corner case that contradicts the hardware).

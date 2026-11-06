<sub>[← back to README](../README.md)</sub>

# Performance

## VU1 optimisation log (one day, every step bit-exact)

Every step is accepted only if a differential run against the cycle-accurate interpreter shows **0 mismatches
over 7.26 million VU1 program invocations** and 36/36 regression screenshots are pixel-identical.

| Step | VU1 thread CPU time | |
|---|---|---|
| Baseline (stage-5 compiler) | 83.2 s | |
| Remaining lower ops translated directly (DIV/RSQRT/SQRT/WAITQ, clip/status/MAC flag tests, MTIR/MFIR/ILWR/XTOP) — 389 interpreter fallbacks in the hot path → 0 | 76.7 s | −8 % |
| Leaner bookkeeping: no upper-opcode tracking in the fast path, dominated "pending result" updates removed per block, branch-VI backup inline | 72.6 s | −13 % |
| Cycle skipping to the next pipeline event; XGKICK copies whole GIF tag payloads instead of 16 bytes at a time | 70.1 s | −16 % |
| Clip flags through a small in-block ring instead of the generic flag pipeline; VIF1 `UNPACK` specialised per format (no mask, CL/WL fast path — 176 M unpacks checked against the generic path) | 69.1 s | −17 % |
| Profile-guided build of the above (`pc143`) | 63.1 s | −24 % |
| Sticky status bits (`0xFC0`) dropped: a whole-program scan proves no VU1 program of this game ever reads them (all `FSAND` masks are `0x002`, `FMAND`/`FMEQ` read MAC, no `FCSET`) — without PGO | 59.0 s | **−29 %** |
| Interpreter → compiled re-entry at the next block entry after a coverage gap (`pc149`) | – | colossus fix |
| **All VU1 code compiled statically** (`pc154`): modules read from the game files, entries from each module's jump table and `MSCNT` continuations, reachability like PCSX2's microVU; programs recognised by a signature of exactly the bytes they compile, so leftovers of previously loaded modules no longer cause misses. 7,666 → **33,837** compiled instruction pairs, 11 → **21** programs | – | ✓ |
| **Static cycle planning** (`pc161`/`pc162`, microVU-style): the cycle counter is accumulated per straight-line run and only materialised where something reads it; readiness marks only for writes that can outlive a run; Q made visible explicitly before its readers; entry checks only for registers that can still be in flight from a predecessor (pipeline state propagated across static branches) | **43.0 s** | **−48 %** vs. the 83 s baseline |
| Tried and rejected: a leaner FMAC core with normalised register shadows ("stage 6") — correct, but 8–15 % *slower* (code size / spills) | 89.8–95.6 s | ✗ |

Measured with the thread's own CPU clock (`CLOCK_THREAD_CPUTIME_ID`), solo, same replay, no PGO noise.

## Texture packs: making them fast

Upscaled PCSX2 texture packs often slow PCSX2 down far more than their size suggests. Two causes were found and
fixed with tools in this repo (they work for any PCSX2 pack, not just this game):

1. **Alpha overshoot.** On the PS2, alpha 128 means fully opaque. Upscalers sharpen alpha edges to 129–135; PCSX2 then
   tracks the render target as "alpha > 1.0" and pushes every later draw that reads destination alpha through the
   shader path with a barrier per draw (3–8× draw calls/barriers). `tools/dds_alpha_fix.py` clamps such overshoot back
   to 128 (only where ≤ 1 % of pixels exceed 140, so genuinely bright alpha stays). Result here: 85.6 → **88.2 FPS**
   (88.8 without any pack), the title menu went from 39–54 to 77–84 FPS, and the image is visually identical.
2. **Replaced palette textures with animated CLUTs.** Replacing a paletted texture makes PCSX2 disable GPU palette
   conversion for *all* palette variants of it; distance-fade palettes then convert every variant on the CPU. A per-pack
   `.ausschluss.txt` (exclude list) skips those few textures.

Also: `dds_mipmaps.py` adds the missing mip chain to BC3/BC7 packs (level 0 bit-identical), `png2dds.py` converts large
PNGs to BC7 with mips, `dds_alpha.py` precomputes the alpha range so PCSX2 no longer decompresses every 4K texture on
the CPU when loading (patched loader in `gsbridge/`).

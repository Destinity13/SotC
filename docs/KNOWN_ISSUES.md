<sub>[← back to README](../README.md)</sub>

# Known issues

An honest list, as of November 2026 (runtime build `pc164`).

## Open

- **Later colossi are not benchmarked yet.** The first colossus fight replays at 76–98 FPS unpaced
  ([PERFORMANCE](PERFORMANCE.md#colossus-benchmark-pc164)); later fights may load VU1 more.
- **Texture packs cost a little more GPU work in the open world** than no pack (≈ 2–4× the render-pass barriers of
  the baseline, after the fixes in [PERFORMANCE.md](PERFORMANCE.md#texture-packs-making-them-fast)). Not visible
  as a frame-rate drop on the development machine.
- **HD text without a texture pack** covers only our own re-typeset German pages (language select, title/bonus
  menus, 50/60 Hz notice, first tutorial boxes, first subtitles). With a pack that ships text pages (Origami: all
  5 languages), the pack's pages are used everywhere.
- **Determinism under heavy load:** one of six replays of a regression route diverged after ~1,400 frames while
  five other runs used the machine; the five repeats were bit-identical. Under investigation.
- **Changing texture settings in the in-game menu reloads all textures**, which pauses the game for a few seconds
  with a large pack.
- The **built-in differential VU1 self-test** (`PS2X_VU1_SELFTEST`) reports false mismatches with the static cycle
  planner; equivalence is currently checked with the decode/replay comparison instead.
- **"New Game" from inside a running game** (soft restart) is untested.
- **Widescreen** is a renderer-side setting; the game's 4:3 camera framing is kept.
- **Unit tests:** 435/446 pass. The 11 failures are test-bed assumptions (old CPU GS path, IOP heap address
  range, a VIF1 corner case that contradicts the hardware, VU1 flag tests that need `PS2X_VU1_FLAGS_ALWAYS=1`).
- **PAL only.** NTSC-U/J would need a new module layout and a re-run of the whole pipeline.
- No rumble yet. Windows is a separate, later project.

## Fixed (kept for the record)

| Build | Problem | Cause and fix |
|---|---|---|
| `pc147` | 60 Hz mode stayed at 50 Hz | the HLE `sceGsResetGraph` never issued the `SetGsCrt` syscall, so the switch to NTSC timing was missed |
| `pc149` | 25–38 FPS in the first colossus fight | VU1 code paths not seen in recordings ran in the interpreter for the rest of the program; the interpreter now re-enters compiled code at the next block entry |
| `pc154` | all VU1 code compiled statically | modules read from the game files, reachability like PCSX2's microVU, 11 → 21 programs compiled |
| `pc157` | colossus frame drops after a few minutes | stale code from previously loaded modules leaked into the program signatures; signatures now cover only the module and the shared library |
| `pc163` | title menu at 39–54 FPS with a texture pack | PS2 alpha overshoot in the pack + replaced palette textures; see [PERFORMANCE.md](PERFORMANCE.md#texture-packs-making-them-fast) |
| `pc164` | warnings for syscalls 13/14 at boot | `SetVTLBRefillHandler` / `SetVCommonHandler` now behave like the BIOS (store per cause, return the previous handler) |
| `pc163` | small per-call overhead in the VU1 path | an environment variable was read on every VU1 call; the play-time VU1 recording was on by default |

<sub>[← back to README](../README.md)</sub>

# Known issues

An honest list, as of November 2026 (runtime build `pc163`).

## Open

- **VU1 headroom in heavy colossus moments.** All VU1 programs are compiled ahead of time, but the VU1 thread
  still needs a large share of the 16.7 ms frame at 60 Hz. Short dips are still possible.
- **Texture packs cost a little more GPU work in the open world** than no pack (≈ 2–4× the render-pass barriers of
  the baseline, after the fixes in [PERFORMANCE.md](PERFORMANCE.md#texture-packs-making-them-fast)). Not visible
  as a frame-rate drop on the development machine.
- **HD text** covers only the pages seen so far (language select, title/bonus menus, 50/60 Hz notice, first
  tutorial boxes, first subtitles) and only German. Other languages and later pages show the original textures.
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
| `pc163` | small per-call overhead in the VU1 path | an environment variable was read on every VU1 call; the play-time VU1 recording was on by default |

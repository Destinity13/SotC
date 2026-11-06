<sub>[← back to README](../README.md)</sub>

# Tools built for this project

All in `tools/`, all ours unless noted:

| Tool | Purpose |
|---|---|
| `extract_iso.py`, `scan_archive_xff.py`, `xff_symbols.py` | pull files and the resource index out of the disc; symbol tables from the XFF modules |
| `link_sotc.py`, `verify_link.py`, `find_load_addresses.sh` | static linker for loader + XFF modules at fixed addresses, with placement report and stub generation for undefined externs |
| `pipeline.sh`, `filter_stubs.py`, `find_missing_targets.py`, `gen_dyn_toml.py` | the ISO→C++ pipeline around the recompiler (two-pass, dynamic call sites, stub filtering) |
| `vu1dump.py`, `vu1dis.py`, `vu1rec.py` | VU1 micro program dumper, disassembler and the ahead-of-time compiler (stages 1–5); static reachability from module jump tables, signature-based program recognition, output split into 16 translation units |
| `vu1_module.py` | Extracts every VU1 micro program module from the game's own files (VIF `MPG` upload chains in `MANAGER.XFF` of *your* dump) so all of them are compiled ahead of time — no play-through needed |
| `vu1dump_merge.py` | Merges VU1 recordings (coverage union, module lengths) |
| `pc_bauen_pgo.sh`, `build_game.sh`, `setup_cachyos.sh` | PGO build on the development machine, environment setup |
| `sotc_worker.sh`, `sotc_bench.sh`, `sotc_test.sh`, `matrix.sh`, `auswerten.sh`, `sotc_test_report.py`, `sotc_dashboard.py`, `sotc_monitor.sh` | job queue: headless replay runs (up to 28 in parallel), experiment matrices, screenshot regression, HTML dashboard |
| `pcsx2_nachspiel.py`, `pcsx2_fernbedienung.py`, `pcsx2_referenz.py` | drive PCSX2 with the same input recording (virtual pad via uinput, PINE socket, invisible compositor), take savestates at chosen frames |
| `spieler_vgl.py`, `obj_felder.py`, `obj_zeit.py`, `kam_diff.py`, `kam_zeit.py`, `kam_find.py`, `kamera_vgl.py`, `ramvgl.py`, `modpos.py`, `modpos2.py`, `hash_vgl.py`, `ref_vergleich.py`, `ref_ref.py`, `mtvu_vgl.py`, `p2s_auswerten.py`, `compare_mem2.py` | compare port RAM dumps with PCSX2 savestates: player, camera, objects, module placement, hash tables, run-to-run jitter |
| `regcheck.py`, `vucheck.py`, `qcheck.py`, `stub_ziel.py`, `stub_ziel2.py` | audits of the generated code (register usage, VU special cases, Q pipeline, what undefined externs do on the console) |
| `profil_aufloesen.py` + in-runtime sampling profiler | per-thread profiles without `perf` |
| `staubemitter.py`, `staubtabelle.py`, `staubdesc.py`, `sublink_felder.py`, `emitter_felder.py`, `ueberlebende.py`, `ppool.py` | particle/emitter forensics (the heap-leak hunt) |
| `chain_walk.py`, `dl_walk.py`, `geodisp_emitter.py`, `loader_xrefs.py`, `str_xrefs.py`, `disasm.py`, `r5900_ext.py` | DMA chain / display list walkers, cross references, R5900 disassembly helpers |
| `textseite_neu.py`, `hd_schrift.py` | HD text: re-typesetting of text-page textures (line/word detection, OCR, corrections, 4× rendering); the earlier edge-shaping experiment |
| `run_game.sh`, `install_worker.sh` | launcher with sane defaults; test worker autostart |
| `installieren.sh` | Linux installer: menu entry + icon, disc-image picker, texture-pack import, `--entfernen` to uninstall |
| `dds_alpha_fix.py`, `dds_alpha.py`, `dds_mipmaps.py`, `dds_kappen.py`, `png2dds.py` | make PCSX2 texture packs fast: clamp PS2 alpha overshoot, precompute alpha ranges (sidecars), add mip chains, cap sizes, PNG → BC7 |
| `profil_minute.py` | per-minute play-session profiles (which code was hot when a frame drop happened) |
| `effekte/*.conf` | vkBasalt presets (*klar*, *filmisch*, *original-plus*) selectable in the in-game menu |

Inside the runtime: input recording/replay, RAM dumps at the pad poll, hardware write watchpoints
with backtraces, a call ring buffer, thread/RPC logs, an FPS/VBlank log and a differential VU1 tester —
all switchable by environment variables and documented in `tools/run_game.sh`.

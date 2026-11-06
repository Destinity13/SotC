"""Summarise one SotC test run: counters from the log, contact sheet, diff against baseline."""
import glob
import os
import re
import sys

from PIL import Image, ImageChops, ImageStat

out, rc, secs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
log = open(os.path.join(out, "log.txt"), errors="ignore").read()

def count(pat):
    return len(re.findall(pat, log))

done = re.search(r"\[SotC test\] done tick=(\d+) seconds=([\d.]+) fps=([\d.]+)", log)
# Count bad-op dump lines (deterministic: emitted on the EE thread) instead of the
# periodic histogram, whose last print depends on host timing.
bad_ops = count(r"\[VIF1 bad\]")

summary = {
    "exit": rc,
    "wall_s": secs,
    "reached_tick": done.group(1) if done else "-",
    "emu_fps": done.group(3) if done else "-",
    "aborts": count(r"\[SotC ABORT\]"),
    "asserts": count(r"\[SotC ASSERT\]"),
    "missing_targets": count(r"missing-target"),
    "bad_chains": count(r"\[VIF1 chain\]"),
    "vif_bad_ops": bad_ops,
    "vu1_reserved": count(r"\[VU1 reserved"),
}

shots = sorted(glob.glob(os.path.join(out, "shot_*.ppm")))
thumbs = []
diffs = []
base_dir = "/tmp/sotc_tests/baseline"
for path in shots:
    im = Image.open(path).convert("RGB")
    im.save(path[:-4] + ".png")
    thumbs.append((os.path.basename(path)[5:10], im.resize((256, 208))))
    ref = os.path.join(base_dir, os.path.basename(path))
    if os.path.isdir(base_dir) and os.path.abspath(out) != os.path.abspath(base_dir) and os.path.exists(ref):
        d = ImageStat.Stat(ImageChops.difference(im, Image.open(ref).convert("RGB"))).mean
        diffs.append((os.path.basename(path)[5:10], round(sum(d) / 3, 1)))
if thumbs:
    cols = 5
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (256 * cols, 222 * rows), (20, 20, 20))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(sheet)
    for i, (tick, im) in enumerate(thumbs):
        x, y = (i % cols) * 256, (i // cols) * 222
        sheet.paste(im, (x, y + 14))
        draw.text((x + 4, y + 1), "tick " + tick.lstrip("0"), fill=(230, 230, 230))
    sheet.save(os.path.join(out, "contact.png"))

lines = [f"{k}={v}" for k, v in summary.items()]
if diffs:
    lines.append("diff_vs_baseline=" + " ".join(f"{t.lstrip('0')}:{d}" for t, d in diffs))
text = " | ".join(lines)
open(os.path.join(out, "summary.txt"), "w").write(text + "\n")
print(f"[{os.path.basename(out)}] {text}")
import json, time
record = dict(summary)
record["name"] = os.path.basename(out)
record["time"] = time.strftime("%Y-%m-%d %H:%M")
record["diffs"] = diffs
json.dump(record, open(os.path.join(out, "summary.json"), "w"))
with open("/tmp/sotc_tests/history.jsonl", "a") as h:
    h.write(json.dumps(record) + "\n")

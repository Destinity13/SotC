"""Inject the test-lab section into the progress page (between <!--TESTS-START--> and <!--TESTS-END-->).

Reads /tmp/sotc_tests/history.jsonl and /home/claude/tools/milestones.json.
Usage: python3 sotc_dashboard.py /home/claude/sotc_status/index.html
"""
import html
import json
import os
import sys

page_path = sys.argv[1]
hist_path = "/tmp/sotc_tests/history.jsonl"
mile_path = "/home/claude/tools/milestones.json"

runs = []
if os.path.exists(hist_path):
    for line in open(hist_path):
        line = line.strip()
        if line:
            runs.append(json.loads(line))
milestones = json.load(open(mile_path)) if os.path.exists(mile_path) else {"items": [], "updated": ""}


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def latest(prefix):
    for r in reversed(runs):
        if r["name"].startswith(prefix):
            return r
    return None


def tile(label, value, note, state=""):
    cls = f"tile {state}".strip()
    return f'<div class="{cls}"><small>{html.escape(label)}</small><b class="num">{html.escape(str(value))}</b><small>{html.escape(note)}</small></div>'


def run_tiles(r, title):
    if not r:
        return f'<p class="muted">{html.escape(title)}: noch kein Lauf.</p>'
    errors = int(num(r["aborts"])) + int(num(r["missing_targets"]))
    gfx = int(num(r["vif_bad_ops"])) + int(num(r["vu1_reserved"])) + int(num(r["bad_chains"]))
    out = [f'<h3>{html.escape(title)} <span class="muted mono" style="font-size:13px">{html.escape(r["time"])}</span></h3>',
           '<div class="tiles">',
           tile("Erreichtes Bild", r["reached_tick"], "VSync-Zähler (50 = 1 s Spielzeit)"),
           tile("Tempo", f'{num(r["emu_fps"]):.1f} fps', "Bilder pro Sekunde in der Cloud, Ziel 50"),
           tile("Abstürze", errors, "Abbrüche + Sprünge ins Leere", "ok" if errors == 0 else "bad"),
           tile("Grafikfehler", gfx, "ungültige VIF-Befehle, VU1-Abbrüche, kaputte Listen", "ok" if gfx == 0 else "warn"),
           '</div>']
    if r.get("diffs"):
        changed = [d for d in r["diffs"] if d[1] > 2.0]
        out.append(f'<p class="muted" style="font-size:14px">Vergleich mit Referenzlauf: {len(changed)} von {len(r["diffs"])} Checkpoints sehen anders aus.</p>')
    return "".join(out)


def trend_svg():
    data = [r for r in runs][-14:]
    if not data:
        return ""
    w, h, pad = 900, 220, 40
    n = len(data)
    step = (w - 2 * pad) / max(1, n)
    max_err = max(1, max(int(num(r["vif_bad_ops"])) + int(num(r["vu1_reserved"])) * 10 + int(num(r["bad_chains"])) * 100 for r in data))
    max_fps = max(10.0, max(num(r["emu_fps"]) for r in data))
    parts = [f'<svg viewBox="0 0 {w} {h + 40}" role="img" aria-label="Verlauf der Testläufe">',
             f'<line x1="{pad}" y1="{h}" x2="{w - pad}" y2="{h}" stroke="#313a40"/>']
    pts = []
    for i, r in enumerate(data):
        x = pad + i * step + step * 0.2
        bw = step * 0.6
        err = int(num(r["vif_bad_ops"])) + int(num(r["vu1_reserved"])) * 10 + int(num(r["bad_chains"])) * 100
        bh = (h - pad) * err / max_err
        crash = int(num(r["aborts"])) + int(num(r["missing_targets"]))
        color = "#de6a60" if crash else "#e3a052"
        parts.append(f'<rect x="{x:.1f}" y="{h - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}" opacity="0.85"/>')
        fy = h - (h - pad) * num(r["emu_fps"]) / max_fps
        pts.append((x + bw / 2, fy))
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{h + 16}" text-anchor="middle" font-size="11" fill="#9ba4a7">{html.escape(r["time"][-5:])}</text>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{h + 30}" text-anchor="middle" font-size="10" fill="#9ba4a7">{html.escape(r["name"][:10])}</text>')
    if len(pts) > 1:
        parts.append('<polyline fill="none" stroke="#6fbf8f" stroke-width="2.5" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + '"/>')
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#6fbf8f"/>')
    parts.append(f'<text x="{pad}" y="16" font-size="12" fill="#9ba4a7">Balken: Grafikfehler (gewichtet, rot = mit Absturz) · Linie: Tempo (max {max_fps:.0f} fps)</text>')
    parts.append("</svg>")
    return '<div class="diagram">' + "".join(parts) + "</div>"


def milestone_row():
    items = milestones.get("items", [])
    if not items:
        return ""
    cells = []
    for m in items:
        state = m.get("state", "open")
        pill = {"ok": ("ok", "erreicht"), "partial": ("now", "mit Fehlern"), "open": ("next", "offen")}[state]
        cells.append(f'<div class="card"><div class="lbl">{html.escape(m["tick"])}</div><h3>{html.escape(m["name"])}</h3>'
                     f'<span class="pill {pill[0]}">{pill[1]}</span><p class="muted" style="font-size:14px">{html.escape(m.get("note", ""))}</p></div>')
    return '<div class="cards">' + "".join(cells) + "</div>"


section = f"""<!--TESTS-START-->
<section>
  <div class="kicker">Testlabor · wird nach jedem Testlauf aktualisiert</div>
  <h2>Automatische Testläufe</h2>
  <p>Jede Änderung läuft durch denselben Ablauf: Das Spiel startet ohne Echtzeit-Bremse, drückt seine Tasten nach Skript an festen Bildnummern und speichert an festen Stellen ein Bild. So ist jeder Lauf mit dem vorigen vergleichbar. Der <b>Schnelltest</b> geht bis zum Titelmenü (etwa 5 Minuten), der <b>Komplett-Test</b> bis in die Spielszene (etwa 10 Minuten).</p>
  <h3>Checkpoints im Spielablauf</h3>
  {milestone_row()}
  {run_tiles(latest("full"), "Letzter Komplett-Test")}
  {run_tiles(latest("quick"), "Letzter Schnelltest")}
  <h3>Verlauf</h3>
  {trend_svg()}
  <div class="cards">
    <div class="card"><div class="lbl">1 · BAUEN</div><p>Nur geänderte Teile neu übersetzen (Sekunden statt 10 Minuten).</p></div>
    <div class="card"><div class="lbl">2 · SCHNELLTEST</div><p>Nach jeder Änderung bis zum Titel. Zwei Varianten laufen parallel.</p></div>
    <div class="card"><div class="lbl">3 · KOMPLETT-TEST</div><p>Bei jedem Meilenstein und mindestens alle paar Änderungen bis in die Spielszene.</p></div>
    <div class="card"><div class="lbl">4 · VERGLEICH</div><p>Checkpoint-Bilder und Fehlerzähler gegen den Referenzlauf; Rückschritte fallen sofort auf.</p></div>
    <div class="card"><div class="lbl">5 · SICHERN</div><p>Code, Doku und diese Seite auf den neuen Stand; Screenshots in den Chat und nach docs/screenshots.</p></div>
  </div>
</section>
<!--TESTS-END-->"""

page = open(page_path).read()
if "<!--TESTS-START-->" in page:
    a = page.index("<!--TESTS-START-->")
    b = page.index("<!--TESTS-END-->") + len("<!--TESTS-END-->")
    page = page[:a] + section + page[b:]
else:
    anchor = '<section>\n  <div class="kicker">Die Idee in einem Bild</div>'
    page = page.replace(anchor, section + "\n\n" + anchor, 1)
open(page_path, "w").write(page)
print("dashboard updated:", len(runs), "runs")

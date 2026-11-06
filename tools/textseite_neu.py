#!/usr/bin/env python3
"""textseite_neu.py – Textseiten-Texturen von SotC ohne KI neu setzen.

Ablauf je Textur:
  1. Zeilen/Textblöcke aus dem Alphakanal der Originaltextur ermitteln (Zeilenbänder, dann Spaltenlücken).
  2. Text je Block per Tesseract lesen (8x hochgerechnet); Korrekturen aus <ausgabe>/texte/<name>.json
     überschreiben die OCR: {"3": "Menü"} (ganzer Block) oder {"3": [{"t": "Menü", "x0": 149, "x1": 198}]}
     (Wörter mit x-Bereich, z. B. wenn ein Tastensymbol im Block steht); "" = Block unverändert lassen.
  3. Jeden Block in SKALIERUNG-facher Auflösung mit der gewählten Schrift neu rendern, Größe und Lage so,
     dass die Blockbox (links/oben/unten) der Originalbox entspricht. Farbe = Mittel der deckenden Pixel.
  4. Ergebnis als PCSX2-Ersatztextur (gleicher Dateiname, SKALIERUNG-fache Größe) ablegen.

Aufruf: textseite_neu.py <ausgabeordner> <schrift.ttf|familie[:stil]> <dump.png> ...
Umgebung: SOTC_TS_SKAL (Standard 4), SOTC_TS_NUR_LESEN=1 (nur OCR ausgeben, nichts rendern),
          SOTC_TS_LANG (Tesseract-Sprachen, Standard deu+eng), SOTC_TS_TESSDATA (Ordner mit traineddata)
"""
import sys, os, json, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SKAL = int(os.environ.get('SOTC_TS_SKAL', '4'))
NUR_LESEN = os.environ.get('SOTC_TS_NUR_LESEN') == '1'
ALPHA_MIN = 48
LANG = os.environ.get('SOTC_TS_LANG', 'deu+eng')
TESS = os.environ.get('SOTC_TS_TESSDATA')  # Ordner mit *.traineddata (z. B. build/texturen/tessdata)


def schrift_datei(spec):
    if os.path.isfile(spec):
        return spec
    return subprocess.run(['fc-match', '-f', '%{file}', spec], capture_output=True, text=True).stdout.strip()


def baender(mask, achse, luecke):
    """zusammenhängende Bereiche (start, ende) entlang einer Achse; Lücken <= luecke werden überbrückt"""
    proj = mask.any(axis=1 - achse) if achse == 0 else mask.any(axis=0)
    out = []
    start = None
    leer = 0
    for i, v in enumerate(proj):
        if v:
            if start is None:
                start = i
            leer = 0
        elif start is not None:
            leer += 1
            if leer > luecke:
                out.append((start, i - leer))
                start = None
                leer = 0
    if start is not None:
        out.append((start, len(proj) - leer))
    return out


def zeilen_teilen(mask, baender_y):
    """Zeilen trennen, die sich über Unter-/Oberlängen berühren: Zeilenkerne (x-Höhe) sind Zeilen mit hoher
    Füllung; zwischen zwei Kernen wird an der dünnsten Stelle getrennt."""
    out = []
    for y0, y1 in baender_y:
        proj = mask[y0:y1].sum(1).astype(float)
        if len(proj) < 6:
            out.append((y0, y1))
            continue
        # Glätten gegen Einzelzeilen-Ausreißer
        g = np.convolve(proj, np.ones(3) / 3.0, mode='same')
        kern = g >= g.max() * 0.45
        kerne = []
        i = 0
        while i < len(kern):
            if kern[i]:
                j = i
                while j < len(kern) and kern[j]:
                    j += 1
                if j - i >= 2:
                    kerne.append((i, j))
                i = j
            else:
                i += 1
        if len(kerne) <= 1:
            out.append((y0, y1))
            continue
        # Kerne, die näher als eine halbe Kernhöhe liegen, gehören zu einer Zeile (z. B. Kern + Oberlänge)
        kh = int(np.median([b - a for a, b in kerne]))
        zus = [list(kerne[0])]
        for a, b in kerne[1:]:
            if a - zus[-1][1] < kh * 0.5:
                zus[-1][1] = b
            else:
                zus.append([a, b])
        schnitte = []
        for (a0, b0), (a1, b1) in zip(zus, zus[1:]):
            schnitte.append(b0 + int(np.argmin(g[b0:a1])) if a1 > b0 else b0)
        grenzen = [0] + schnitte + [len(proj)]
        for a, b in zip(grenzen, grenzen[1:]):
            if b - a >= 2:
                out.append((y0 + a, y0 + b))
    return luecken_teilen(mask, out)


def luecken_teilen(mask, baender_y):
    """zweiter Durchgang: sehr dünn gefüllte Zeilen (Unterlängen, < 8 % des Bandmaximums) schließen eine Zeile ab"""
    out = []
    for y0, y1 in baender_y:
        proj = mask[y0:y1].sum(1)
        if len(proj) < 8:
            out.append((y0, y1))
            continue
        schwelle = max(1, proj.max() * 0.08)
        niedrig = proj < schwelle
        start = 0
        i = 0
        while i < len(proj):
            if niedrig[i] and i > start + 3:
                j = i
                while j < len(proj) and niedrig[j]:
                    j += 1
                if j < len(proj) - 2:
                    out.append((y0 + start, y0 + j))
                    start = j
                i = j
            else:
                i += 1
        out.append((y0 + start, y1))
    return out


def ist_text(t):
    alnum = sum(c.isalnum() for c in t)
    alpha = sum(c.isalpha() for c in t)
    return len(t) >= 1 and alnum >= max(1, 0.6 * len(t.replace(' ', ''))) and (alpha >= 1 or t.isdigit())


def bloecke_aus_maske(mask, dx=0, dy=0, zeilen_start=0):
    res = []
    zeilen = []
    for y0, y1 in zeilen_teilen(mask, baender(mask, 0, 0)):
        h = y1 - y0
        if h < 3:
            continue
        zeile = mask[y0:y1]
        # Wortlücken: Buchstabenlücken (< 0.35 Zeilenhöhe) bleiben im Block, größere trennen Wörter/Symbole.
        # Jedes Wort wird einzeln gesetzt, damit die Lage der Originalvorlage folgt (Symbole bleiben Original).
        for x0, x1 in baender(zeile, 1, max(1, int(h * 0.35))):
            if x1 - x0 < 2:
                continue
            sub = mask[y0:y1, x0:x1]
            ys = np.where(sub.any(axis=1))[0]
            res.append((x0 + dx, y0 + ys[0] + dy, x1 + dx, y0 + ys[-1] + 1 + dy, zeilen_start + len(zeilen)))
        zeilen.append((y0, y1))
    return res, len(zeilen)


def bloecke(arr):
    """Textblöcke (x0,y0,x1,y1,zeile). Liefert zusätzlich die Liste der 'Kästen' (gefüllte Flächen mit Text
    darin, z. B. Hinweisboxen): dort wird der Text über die Helligkeit gefunden."""
    alpha = arr[:, :, 3]
    mask = alpha > ALPHA_MIN
    grob, nz = bloecke_aus_maske(mask)
    res = []
    kaesten = []
    for b in grob:
        x0, y0, x1, y1, z = b
        fl = mask[y0:y1, x0:x1].mean()
        if fl > 0.92 and (x1 - x0) >= 40 and (y1 - y0) >= 16:
            # Kasten: Text = Pixel deutlich heller als der Kastenhintergrund
            teil = arr[y0:y1, x0:x1, :3].astype(float)
            lum = teil.mean(2)
            grund = np.median(lum)
            tm_hell = lum > grund + max(24.0, (lum.max() - grund) * 0.45)
            # Textmaske für den Hintergrund: alles, was vom Kastengrund abweicht (auch dunkle Kontur/Schatten)
            tm = np.abs(lum - grund) > max(10.0, (lum.max() - grund) * 0.15)
            tm_text = tm_hell
            innen, nzi = bloecke_aus_maske(tm_text, x0, y0, nz)
            nz += nzi
            symbole, _ = bloecke_aus_maske(tm, x0, y0, 0)  # alle abweichenden Flächen (Text + Symbole)
            kaesten.append((x0, y0, x1, y1, tm, symbole))
            res.extend(innen)
        else:
            res.append(b)
    return res, kaesten


def ocr(img, box):
    """Wörter eines Blocks lesen: Liste (text, (x0,y0,x1,y1)) in Texturkoordinaten; Wörter mit geringer
    Tesseract-Sicherheit (Symbole, Linien, CJK) werden weggelassen."""
    x0, y0, x1, y1 = box[:4]
    r = 8
    rx, ry = max(0, x0 - 2), max(0, y0 - 2)
    teil = img.crop((rx, ry, x1 + 2, y1 + 2))
    bg = Image.new('RGBA', teil.size, (0, 0, 0, 255))
    bg.paste(teil, (0, 0), teil)
    g = bg.convert('L').resize((teil.width * r, teil.height * r), Image.LANCZOS)
    g = Image.eval(g, lambda v: 255 - v)  # dunkler Text auf hell
    p = '/tmp/_ts_ocr.png'
    g.save(p)
    env = dict(os.environ, TESSDATA_PREFIX=TESS) if TESS else None
    t = subprocess.run(['tesseract', p, '-', '--psm', '7', '-l', LANG, 'tsv'], capture_output=True, text=True, env=env).stdout
    woerter = []
    for zeile in t.splitlines()[1:]:
        f = zeile.split('\t')
        if len(f) >= 12 and f[0] == '5' and f[11].strip() and float(f[10]) >= 55:
            wx, wy, ww, wh = (int(v) for v in f[6:10])
            wb = (rx + wx / r, ry + wy / r, rx + (wx + ww) / r, ry + (wy + wh) / r)
            woerter.append((f[11].strip(), tuple(int(round(v)) for v in wb)))
    return woerter


def farben(arr, box):
    """(Schriftfarbe, Randfarbe|None): helle Mehrheit = Schrift; gibt es daneben deutlich dunklere deckende
    Pixel (Kontur/Schatten), wird deren Mittel als Randfarbe geliefert."""
    x0, y0, x1, y1 = box[:4]
    a = arr[y0:y1, x0:x1]
    deck = a[:, :, 3] > 160
    if deck.sum() < 4:
        deck = a[:, :, 3] > ALPHA_MIN
    rgb = a[:, :, :3][deck].astype(float)
    hell = rgb.sum(1)
    p80 = np.percentile(hell, 80)
    schrift = rgb[hell >= p80 * 0.9]
    sf = tuple(int(v) for v in schrift.mean(0))
    dunkel = rgb[hell < p80 * 0.45]
    rand = None
    if len(dunkel) >= 0.15 * len(rgb):
        rand = tuple(int(v) for v in dunkel.mean(0))
    return sf, rand


def passe_groesse(font_path, text, ziel_h, ziel_w):
    """größte Schriftgröße, bei der der gesetzte Text weder höher als ziel_h noch breiter als ziel_w*1.04 ist"""
    lo, hi = 4, 400
    best = lo
    while lo <= hi:
        m = (lo + hi) // 2
        f = ImageFont.truetype(font_path, m)
        bb = f.getbbox(text)
        if bb[3] - bb[1] <= ziel_h and bb[2] - bb[0] <= ziel_w * 1.04:
            best = m
            lo = m + 1
        else:
            hi = m - 1
    return best


def setze(name, img, font_path, aus, korr):
    arr = np.array(img)
    bl, kaesten = bloecke(arr)
    # Wörter: Korrektur ("i": "text" ersetzt den ganzen Block durch ein Wort in Blockbox) oder OCR-Wortboxen
    woerter = []  # (text, box, zeile)
    for i, b in enumerate(bl):
        t = korr.get(str(i)) if korr else None
        if isinstance(t, str):
            ws = [(t, b[:4])] if t else []          # ganzer Block = ein Text
        elif isinstance(t, list):
            # Wortliste mit x-Bereich: [{"t": "Menü", "x0": 149, "x1": 198}, ...] (y aus dem Block)
            ws = [(w['t'], (int(w['x0']), b[1], int(w['x1']), b[3])) for w in t]
        else:
            ws = ocr(img, b)
        ws = [(t, wb) for t, wb in ws if ist_text(t)]
        print(f'  [{i}] {tuple(int(v) for v in b[:4])} ' + ' | '.join(f'{t!r}@{wb[0]},{wb[2]}' for t, wb in ws))
        for t, wb in ws:
            woerter.append((t, wb, b[4], b[:4]))
    if NUR_LESEN:
        return
    W, H = img.size
    out = Image.new('RGBA', (W * SKAL, H * SKAL), (0, 0, 0, 0))
    # 1) Original hochgerechnet als Grundlage (Symbole, Linien, Kästen bleiben so erhalten)
    out.paste(img.resize((W * SKAL, H * SKAL), Image.BICUBIC), (0, 0))
    # 2) Kästen: Hintergrund ohne alten Text (Textpixel + 1 px Rand durch Zeilenmedian der Restpixel ersetzen)
    for x0, y0, x1, y1, tm, symbole in kaesten:
        teil = arr[y0:y1, x0:x1].copy()
        tmd = tm.copy()
        for _ in range(2):  # 2 px ausdehnen
            e = tmd.copy()
            e[1:] |= tmd[:-1]; e[:-1] |= tmd[1:]; e[:, 1:] |= tmd[:, :-1]; e[:, :-1] |= tmd[:, 1:]
            tmd = e
        for r in range(teil.shape[0]):
            rest = teil[r][~tmd[r]]
            if len(rest):
                teil[r][tmd[r]] = np.median(rest, axis=0).astype(np.uint8)
        kimg = Image.fromarray(teil, 'RGBA').resize(((x1 - x0) * SKAL, (y1 - y0) * SKAL), Image.BICUBIC)
        out.paste(kimg, (x0 * SKAL, y0 * SKAL))
        # Symbole im Kasten: Blockflächen wieder aus dem Original übernehmen, Wortflächen davon erneut mit
        # Hintergrund füllen (so bleiben nur Tastensymbole u. ä. stehen)
        for sb in symbole:
            bx0, by0, bx1, by1 = max(sb[0] - 1, x0), max(sb[1] - 1, y0), min(sb[2] + 1, x1), min(sb[3] + 1, y1)
            orig = img.crop((bx0, by0, bx1, by1)).resize(((bx1 - bx0) * SKAL, (by1 - by0) * SKAL), Image.BICUBIC)
            out.paste(orig, (bx0 * SKAL, by0 * SKAL))
        for t, wb, z, bb in woerter:
            if x0 <= wb[0] and wb[2] <= x1 and y0 <= wb[1] and wb[3] <= y1:
                wx0, wy0, wx1, wy1 = max(wb[0] - 3, x0), max(bb[1] - 3, y0), min(wb[2] + 3, x1), min(bb[3] + 3, y1)
                bgteil = kimg.crop(((wx0 - x0) * SKAL, (wy0 - y0) * SKAL, (wx1 - x0) * SKAL, (wy1 - y0) * SKAL))
                out.paste(bgteil, (wx0 * SKAL, wy0 * SKAL))
    # 3) alte Wortflächen außerhalb von Kästen löschen (Alpha 0), innerhalb von Kästen sind sie schon ersetzt
    def im_kasten(wb):
        return any(k[0] <= wb[0] and wb[2] <= k[2] and k[1] <= wb[1] and wb[3] <= k[3] for k in kaesten)
    for t, wb, z, bb in woerter:
        if not im_kasten(wb):
            # volle Zeilenhöhe des Blocks und 2 px seitlich, damit auch Kantenpixel des alten Worts verschwinden
            x0, y0, x1, y1 = max(0, wb[0] - 2), max(0, bb[1] - 1), min(W, wb[2] + 2), min(H, bb[3] + 1)
            leer = Image.new('RGBA', ((x1 - x0) * SKAL, (y1 - y0) * SKAL), (0, 0, 0, 0))
            out.paste(leer, (x0 * SKAL, y0 * SKAL))
    # 4) Neusatz je Zeile: eine Größe (Median der Wortanpassungen), eine Grundlinie
    d = ImageDraw.Draw(out)
    UNTEN = set('gjpqyQJ,;()[]')
    zeilen = {}
    for t, wb, z, bb in woerter:
        zeilen.setdefault(z, []).append((t, wb))
    for z, ws in zeilen.items():
        groessen = sorted(passe_groesse(font_path, t, (wb[3] - wb[1]) * SKAL, (wb[2] - wb[0]) * SKAL) for t, wb in ws)
        groesse = groessen[len(groessen) // 2]
        f = ImageFont.truetype(font_path, groesse)
        asc, desc = f.getmetrics()
        ohne = [wb[3] for t, wb in ws if not (set(t) & UNTEN)]
        mit = [wb[3] for t, wb in ws if set(t) & UNTEN]
        grund = int(np.median(ohne)) * SKAL if ohne else int(np.median(mit)) * SKAL - desc
        # Eine Farbe je Zeile (Median ueber die Woerter): einzeln gemittelt kippten manche Woerter ins Lila
        fz = [farben(arr, wb) for t, wb in ws]
        sf_z = tuple(int(np.median([c[0][k] for c in fz])) for k in range(3))
        rz = [c[1] for c in fz if c[1] is not None]
        rand_z = tuple(int(np.median([c[k] for c in rz])) for k in range(3)) if len(rz) * 2 >= len(fz) else None
        # Gleichmaessige Wortabstaende: Woerter in Lesereihenfolge von der linken Kante der Zeile an mit einem
        # einheitlichen Abstand setzen (Originalpositionen schwankten durch die Erkennung: doppelte/fehlende Luecken).
        # Ist die Zeile so breiter als im Original, alles gemeinsam stauchen; die Zeilenmitte bleibt.
        ws = sorted(ws, key=lambda e: e[1][0])
        sw0 = max(1, SKAL // 2) if rand_z is not None else 0
        luecke = f.getlength(' ')
        zh = max(wb[3] - wb[1] for t, wb in ws)
        # Abschnitte: eine Luecke >= 0.8 Zeilenhoehe enthaelt ein Tastensymbol (bleibt im Original stehen) -> dort trennen
        abschn = [[ws[0]]]
        for prev, cur in zip(ws, ws[1:]):
            if cur[1][0] - prev[1][2] >= 0.8 * zh:
                abschn.append([cur])
            else:
                abschn[-1].append(cur)
        ziel_x = []; stauche = []
        for ab in abschn:
            breiten = [f.getbbox(t, anchor='ls', stroke_width=sw0) for t, wb in ab]
            z0, z1 = ab[0][1][0] * SKAL, ab[-1][1][2] * SKAL
            gesamt = sum(bb_[2] - bb_[0] for bb_ in breiten) + luecke * (len(ab) - 1)
            st = min(1.0, (z1 - z0) / gesamt) if gesamt > 0 else 1.0
            # linksbuendig im Abschnitt, wenn er an ein Symbol grenzt, sonst mittig (Zeilenmitte bleibt)
            x = z0 if len(abschn) > 1 else z0 + ((z1 - z0) - gesamt * st) / 2
            for bb_ in breiten:
                ziel_x.append(x); stauche.append(st)
                x += ((bb_[2] - bb_[0]) + luecke) * st
        for (t, wb), zx, stauch in zip(ws, ziel_x, stauche):
            sf, rand = sf_z, rand_z
            sw = sw0
            bb = f.getbbox(t, anchor='ls', stroke_width=sw)
            # Wort einzeln setzen; ist es breiter als die Originalbox, waagerecht stauchen (Höhe bleibt)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            wimg = Image.new('RGBA', (tw + 2, th + 2), (0, 0, 0, 0))
            wd = ImageDraw.Draw(wimg)
            wpos = (1 - bb[0], 1 - bb[1])
            if rand is not None:
                wd.text(wpos, t, font=f, fill=rand + (255,), anchor='ls', stroke_width=sw, stroke_fill=rand + (255,))
            wd.text(wpos, t, font=f, fill=sf + (255,), anchor='ls')
            if stauch < 0.999:
                wimg = wimg.resize((max(1, int(wimg.width * stauch)), wimg.height), Image.LANCZOS)
            out.alpha_composite(wimg, (max(0, int(zx) - 1), grund + bb[1] - 1))
    out.save(os.path.join(aus, name))


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    aus, schrift = sys.argv[1], sys.argv[2]
    os.makedirs(aus, exist_ok=True)
    os.makedirs(os.path.join(aus, 'texte'), exist_ok=True)
    fp = schrift_datei(schrift)
    print('Schrift:', fp)
    for p in sys.argv[3:]:
        name = os.path.basename(p)
        img = Image.open(p).convert('RGBA')
        kp = os.path.join(aus, 'texte', name[:-4] + '.json')
        korr = json.load(open(kp)) if os.path.isfile(kp) else {}
        print(name, img.size, '(Korrekturen: %d)' % len(korr))
        setze(name, img, fp, aus, korr)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Actualiza data.json con los resultados de las loterías dominicanas.

Fuente: https://enloteria.com/resultados-loterias-hoy (portal informativo, no oficial).
Lee los bloques JSON-LD de la página, extrae los 3 premios de cada sorteo
vigilado y reescribe data.json. Diseñado para correr en GitHub Actions
varias veces al día; también funciona localmente.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE_URL = "https://enloteria.com/resultados-loterias-hoy"
DR_TZ = timezone(timedelta(hours=-4))  # Santo Domingo, sin horario de verano

# name en JSON-LD -> (id interno, nombre visible, hora del sorteo)
WATCHLIST = {
    "La Primera":       ("la-primera",      "La Primera",       "12:00 PM"),
    "LoteDom":          ("lotedom",         "LoteDom",          "12:00 PM"),
    "La Suerte":        ("la-suerte",       "La Suerte",        "12:30 PM"),
    "Real":             ("real",            "Real",             "12:55 PM"),
    "Loteka Tarde":     ("loteka-tarde",    "Loteka Tarde",     "1:30 PM"),
    "Nacional Gana Más":("gana-mas",        "Nacional Gana Más","2:30 PM"),
    "La Suerte 6PM":    ("la-suerte-noche", "La Suerte Noche",  "6:00 PM"),
    "La Primera Noche": ("la-primera-noche","La Primera Noche", "7:00 PM"),
    "Loteka":           ("loteka",          "Loteka",           "7:50 PM"),
    "Real Noche":       ("real-noche",      "Real Noche",       "8:00 PM"),
    "Leidsa":           ("leidsa",          "Leidsa",           "8:50 PM"),
    "Nacional Noche":   ("nacional-noche",  "Nacional Noche",   "9:00 PM"),
}

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_events(html: str):
    """Devuelve dict name -> evento desde los bloques JSON-LD."""
    events = {}
    for m in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        items = data.get("@graph") if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Event" and item.get("name"):
                events[item["name"]] = item
    return events


def parse_event(ev: dict):
    """Extrae fecha y números; (date_iso, [n1,n2,n3]) o (date_iso, None) si pendiente."""
    props = {p.get("name"): p.get("value") for p in ev.get("additionalProperty", [])
             if isinstance(p, dict)}
    date_iso = props.get("Fecha del Sorteo")
    if not date_iso:
        sd = ev.get("startDate", "")
        date_iso = sd[:10] if len(sd) >= 10 else None
    nums = [props.get("Primer Premio"), props.get("Segundo Premio"), props.get("Tercer Premio")]
    if all(n for n in nums):
        return date_iso, [str(n).zfill(2) for n in nums]
    return date_iso, None


def main() -> int:
    html = fetch_html(SOURCE_URL)
    events = extract_events(html)
    if not events:
        print("ERROR: no se encontraron eventos JSON-LD en la fuente", file=sys.stderr)
        return 1

    today = datetime.now(DR_TZ).date().isoformat()
    results = []
    found = 0
    for src_name, (rid, name, draw_time) in WATCHLIST.items():
        ev = events.get(src_name)
        if not ev:
            print(f"WARN: sorteo no encontrado en la fuente: {src_name}", file=sys.stderr)
            continue
        date_iso, nums = parse_event(ev)
        found += 1
        results.append({
            "id": rid,
            "name": name,
            "draw_time": draw_time,
            "date": date_iso or today,
            "numbers": nums or [],
            "status": "done" if nums else "pending",
        })

    if found == 0:
        print("ERROR: ningún sorteo de la lista apareció en la fuente", file=sys.stderr)
        return 1

    data = {
        "date": today,
        "updated_at": datetime.now(DR_TZ).isoformat(timespec="seconds"),
        "source": SOURCE_URL,
        "results": results,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"OK: {found}/{len(WATCHLIST)} sorteos actualizados ({today})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Actualiza data.json con los resultados de las loterías dominicanas.

Fuente: https://enloteria.com/resultados-loterias-hoy (portal informativo, no oficial).
Lee los bloques JSON-LD de la página y extrae los 3 premios de cada sorteo.

Reglas importantes:
- NUNCA borra datos: si un sorteo todavía no sale hoy, conserva el último
  resultado conocido (con su fecha visible). Solo reemplaza cuando la fuente
  trae números nuevos del día.
- Reintentos: si un sorteo ya debió salir (hora del sorteo + 30 min de gracia
  para publicación) y la fuente aún no trae sus números, espera 15 minutos
  y vuelve a buscar, hasta 6 intentos (90 min).

Diseñado para correr en GitHub Actions varias veces al día; también funciona
localmente.
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE_URL = "https://enloteria.com/resultados-loterias-hoy"
DR_TZ = timezone(timedelta(hours=-4))  # Santo Domingo, sin horario de verano

# name en JSON-LD -> (id interno, nombre visible, hora del sorteo, minutos desde medianoche)
WATCHLIST = {
    "La Primera":        ("la-primera",       "La Primera",        "12:00 PM", 12 * 60),
    "LoteDom":           ("lotedom",          "LoteDom",           "12:00 PM", 12 * 60),
    "La Suerte":         ("la-suerte",        "La Suerte",         "12:30 PM", 12 * 60 + 30),
    "Real":              ("real",             "Real",              "12:55 PM", 12 * 60 + 55),
    "Loteka Tarde":      ("loteka-tarde",     "Loteka Tarde",      "1:30 PM",  13 * 60 + 30),
    "Nacional Gana Más": ("gana-mas",         "Nacional Gana Más", "2:30 PM",  14 * 60 + 30),
    "La Suerte 6PM":     ("la-suerte-noche",  "La Suerte Noche",   "6:00 PM",  18 * 60),
    "La Primera Noche":  ("la-primera-noche", "La Primera Noche",  "7:00 PM",  19 * 60),
    "Loteka":            ("loteka",           "Loteka",            "7:50 PM",  19 * 60 + 50),
    "Real Noche":        ("real-noche",       "Real Noche",        "8:00 PM",  20 * 60),
    "Leidsa":            ("leidsa",           "Leidsa",            "8:50 PM",  20 * 60 + 50),
    "Nacional Noche":    ("nacional-noche",   "Nacional Noche",    "9:00 PM",  21 * 60),
}

GRACE_MIN = 5         # minutos tras la hora del sorteo para considerarlo "debió salir ya"
RETRY_EVERY_MIN = 15  # espera entre reintentos
MAX_RETRIES = 6       # hasta 90 minutos de reintentos

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# --- Loterías de EE. UU. (NY Open Data, funciona sin API key) ---
PB_URL = "https://data.ny.gov/resource/d6yy-54nr.json?$limit=3&$order=draw_date%20DESC"
MM_URL = "https://data.ny.gov/resource/5xaw-6ayf.json?$limit=3&$order=draw_date%20DESC"

US_GAMES = {
    "powerball":     {"name": "Powerball",     "draw_time": "10:59 PM", "draw_days": "Lun · Mié · Sáb"},
    "mega-millions": {"name": "Mega Millions", "draw_time": "11:00 PM", "draw_days": "Mar · Vie"},
}

US_KEEP = 2  # últimos 2 sorteos por juego


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_powerball(rows):
    """winning_numbers: "16 23 32 36 54 09" = 5 blancas + Powerball al final."""
    out = []
    g = US_GAMES["powerball"]
    for row in rows:
        date = (row.get("draw_date") or "")[:10]
        nums = (row.get("winning_numbers") or "").split()
        if len(date) != 10 or len(nums) < 6:
            continue
        out.append({"id": "powerball", "name": g["name"],
                    "draw_time": g["draw_time"], "draw_days": g["draw_days"],
                    "date": date, "numbers": nums[:5], "bonus": nums[5],
                    "multiplier": row.get("multiplier"), "status": "done"})
    return out


def parse_megamillions(rows):
    """winning_numbers: 5 blancas; mega_ball por separado."""
    out = []
    g = US_GAMES["mega-millions"]
    for row in rows:
        date = (row.get("draw_date") or "")[:10]
        nums = (row.get("winning_numbers") or "").split()
        bonus = (row.get("mega_ball") or "").strip()
        if len(date) != 10 or len(nums) < 5 or not bonus:
            continue
        out.append({"id": "mega-millions", "name": g["name"],
                    "draw_time": g["draw_time"], "draw_days": g["draw_days"],
                    "date": date, "numbers": nums[:5], "bonus": bonus,
                    "multiplier": None, "status": "done"})
    return out


def fetch_us_lotteries():
    """Últimos sorteos de Powerball y Mega Millions desde NY Open Data."""
    fresh = []
    try:
        fresh += parse_powerball(fetch_json(PB_URL))
    except Exception as e:
        print(f"WARN: no se pudo obtener Powerball: {e}", file=sys.stderr)
    try:
        fresh += parse_megamillions(fetch_json(MM_URL))
    except Exception as e:
        print(f"WARN: no se pudo obtener Mega Millions: {e}", file=sys.stderr)
    return fresh


def load_previous_us(path="data.json"):
    """Carga la sección us_lotteries del data.json anterior. [] si no existe."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("us_lotteries", []) or []
    except (FileNotFoundError, json.JSONDecodeError, KeyError, AttributeError):
        return []


def merge_us_lotteries(prev, fresh):
    """Fusión: lo nuevo de la API gana; conserva anteriores (máx US_KEEP por juego).
    Si la API falla, se conserva todo lo anterior: nunca borra."""
    seen = set()
    merged = []
    for entry in fresh + prev:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("id"), entry.get("date"))
        if key in seen or not entry.get("date"):
            continue
        seen.add(key)
        merged.append(entry)
    out = []
    for gid in ("powerball", "mega-millions"):
        group = sorted((e for e in merged if e.get("id") == gid),
                       key=lambda e: e["date"], reverse=True)
        out.extend(group[:US_KEEP])
    return out


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
    """Extrae (fecha_iso, [n1,n2,n3]) o (fecha_iso, None) si aún no hay números."""
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


def load_previous(path="data.json"):
    """Carga el data.json anterior como dict id -> entrada. {} si no existe."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {r["id"]: r for r in data.get("results", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}


def fetch_today():
    """Devuelve (today_iso, {id: (date_iso, numbers|None)}) con lo que trae la fuente hoy."""
    today = datetime.now(DR_TZ).date().isoformat()
    try:
        events = extract_events(fetch_html(SOURCE_URL))
    except Exception as e:
        print(f"WARN: no se pudo descargar la fuente: {e}", file=sys.stderr)
        return today, {}
    fresh = {}
    for src_name, (rid, _name, _dt, _mins) in WATCHLIST.items():
        ev = events.get(src_name)
        if not ev:
            print(f"WARN: sorteo no encontrado en la fuente: {src_name}", file=sys.stderr)
            continue
        date_iso, nums = parse_event(ev)
        # Solo cuenta como resultado de hoy si la fecha coincide y hay números
        if date_iso == today and nums:
            fresh[rid] = (date_iso, nums)
        else:
            fresh[rid] = (date_iso, None)
    return today, fresh


def missing_expected(today, fresh):
    """IDs que ya debieron salir (hora + gracia) pero sin números de hoy."""
    now = datetime.now(DR_TZ)
    now_mins = now.hour * 60 + now.minute
    missing = []
    for _src, (rid, _name, _dt, draw_mins) in WATCHLIST.items():
        if draw_mins + GRACE_MIN <= now_mins:
            got = fresh.get(rid)
            if not got or not got[1]:
                missing.append(rid)
    return missing


def main() -> int:
    prev = load_previous()
    today, fresh = fetch_today()

    # Reintentos cada 15 min para sorteos que ya debieron salir
    attempt = 0
    while attempt < MAX_RETRIES:
        missing = missing_expected(today, fresh)
        if not missing:
            break
        attempt += 1
        print(f"Intento {attempt}/{MAX_RETRIES}: faltan {len(missing)} sorteos "
              f"({', '.join(missing)}); reintentando en {RETRY_EVERY_MIN} min…")
        time.sleep(RETRY_EVERY_MIN * 60)
        today, fresh = fetch_today()
    else:
        missing = missing_expected(today, fresh)

    if missing:
        print(f"AVISO: tras los reintentos siguen sin datos: {', '.join(missing)}; "
              f"se conservan los anteriores.", file=sys.stderr)

    # Fusión: lo nuevo reemplaza, lo que falta se conserva
    results = []
    updated = 0
    kept = 0
    for _src, (rid, name, draw_time, _mins) in WATCHLIST.items():
        got = fresh.get(rid)
        if got and got[1]:
            results.append({"id": rid, "name": name, "draw_time": draw_time,
                            "date": got[0], "numbers": got[1], "status": "done"})
            updated += 1
        elif rid in prev:
            results.append(prev[rid])  # conserva el último resultado conocido
            kept += 1
        else:
            results.append({"id": rid, "name": name, "draw_time": draw_time,
                            "date": today, "numbers": [], "status": "pending"})

    data = {
        "date": today,
        "updated_at": datetime.now(DR_TZ).isoformat(timespec="seconds"),
        "source": SOURCE_URL,
        "results": results,
        "us_lotteries": merge_us_lotteries(load_previous_us(), fetch_us_lotteries()),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"OK: {updated} actualizados, {kept} conservados ({today})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

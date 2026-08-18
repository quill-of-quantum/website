import os
import re
import time

import requests
from bs4 import BeautifulSoup

from modules.housing.db import (
    get_geocode_cache, put_geocode_cache, rooms_pending_geocode,
    save_room_coordinates,
)


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SWKA-Housing-Map/1.0 (personal local housing analysis)"
GEOCODE_DELAY_SECONDS = 1.1
GEOCODE_PROXY = os.getenv("HOUSING_GEOCODE_PROXY", "http://127.0.0.1:7890")
UNKNOWN = "?"
EQUIPMENT_LABELS = {
    "Bad": "浴室", "Dusche": "淋浴", "WC": "卫生间", "Waschbecken im Zimmer": "房内洗手池",
    "Balkon": "阳台", "Einbauküche": "嵌入式厨房", "Kochplatte": "灶台",
    "Kochnische/-ecke": "小厨房", "Kühlschrank": "冰箱", "Spüle": "水槽",
    "Spülmaschine": "洗碗机", "Waschmaschine": "洗衣机", "Trockner": "烘干机",
    "Telefonanschluss": "电话接口", "Internetanschluss": "网络接口", "TV-Anschluss": "电视接口",
    "Nur Mann": "接受男性", "Nur Frau": "接受女性", "Paar": "接受情侣",
    "Nichtraucher": "接受非吸烟者", "Musikstudierende": "音乐专业学生",
}


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def classify_room(room_type):
    text = clean(room_type)
    if re.search(r"\b(?:in\s+\d+er\s+WG|WG-Zimmer|Wohngemeinschaft)\b", text, re.I):
        return "wg"
    if re.search(r"\bEinzelzimmer\b", text, re.I):
        return "einzelzimmer"
    if re.search(r"\d+(?:[.,]\d+)?-Zimmer-Wohnung\b", text, re.I):
        return "xzimmer"
    return "unknown"


def section_html(soup, heading):
    node = soup.find("b", string=lambda value: clean(value).rstrip(":") == heading.rstrip(":"))
    if not node:
        return ""
    parts = []
    for sibling in node.next_siblings:
        if getattr(sibling, "name", None) in {"table", "div"}:
            break
        parts.append(str(sibling))
    return " ".join(BeautifulSoup("".join(parts), "html.parser").stripped_strings)


def euro_value(value):
    match = re.search(r"(\d+(?:[.,]\d+)?)", clean(value).replace(" ", ""))
    return float(match.group(1).replace(",", ".")) if match else None


def money(value):
    if value is None:
        return UNKNOWN
    return f"{int(value)} €" if value.is_integer() else f"{value:.2f} €"


def ox(value):
    return "o" if value is True else "x" if value is False else UNKNOWN


def parse_equipment(soup):
    result = {}
    table = soup.select_one("table.tabDetail")
    if not table:
        return result
    for item in table.select("li.plus, li.minus"):
        name = clean(item.get_text(" ", strip=True))
        result[EQUIPMENT_LABELS.get(name, name)] = "o" if "plus" in (item.get("class") or []) else "x"
    return result


def parse_costs(fields, full_text, equipment):
    rent_match = re.search(r"\bMiete:\s*([\d.,]+\s*€)", full_text, re.I)
    rent_text = clean(rent_match.group(1)) if rent_match else UNKNOWN
    rent = euro_value(rent_text)
    nk_text = clean(fields.get("Nebenkosten (Pauschale)") or fields.get("Nebenkosten (Vorauszahlung)") or UNKNOWN)
    nk = euro_value(nk_text)
    heating_text = clean(fields.get("Heizkosten") or UNKNOWN)
    heating_lower = heating_text.lower()
    heating_included = True if "inklusiv" in heating_lower else False if "extra" in heating_lower else None
    heating_amount = euro_value(heating_text)
    warm_base = rent + (nk or 0) if rent is not None else None
    if warm_base is None:
        warm = UNKNOWN
    elif heating_included is True:
        warm = money(warm_base)
    elif heating_amount is not None:
        warm = money(warm_base + heating_amount)
    elif heating_included is False:
        warm = f"至少 {money(warm_base)} + 取暖费"
    else:
        warm = f"至少 {money(warm_base)}（取暖费未知）"
    electricity = clean(fields.get("Strom") or UNKNOWN).lower()
    electricity_included = True if "inklusiv" in electricity else False if "extra" in electricity else None
    water_match = re.search(r"Wasser\s*:\s*([^:]{1,40}?)(?=\s+[A-ZÄÖÜ][\wÄÖÜäöüß /-]{1,30}:|$)", full_text)
    water = clean(water_match.group(1)) if water_match else UNKNOWN
    water_lower = water.lower()
    water_included = True if "inklusiv" in water_lower else False if "extra" in water_lower else None
    wifi = True if re.search(r"(?:wlan|wi[ -]?fi|internet).{0,20}(?:inklusive|inkl\.)", full_text, re.I) else None
    return {"冷租": rent_text, "杂费": nk_text, "暖租": warm, "取暖费包含": ox(heating_included),
            "电费包含": ox(electricity_included), "水费包含": ox(water_included),
            "Wi-Fi费用包含": ox(wifi), "网络接口": equipment.get("网络接口", UNKNOWN)}


def parse_landlord(fields, full_text, soup):
    landlord_match = re.search(r"Vermieter/-in\s*(.*?)(?=(?:Telefon|Mobiltelefon|E-Mail)\s*:|\s+Zur Übersicht)", full_text, re.I)
    landlord = clean(landlord_match.group(1)) if landlord_match else UNKNOWN
    contact = clean(fields.get("Telefon") or fields.get("Vermieter/-in Telefon") or "")
    phone_match = re.search(r"^(.*?)(?=Mobiltelefon\s*:|$)", contact, re.I)
    mobile_match = re.search(r"Mobiltelefon\s*:\s*(.*?)(?=\s+E-Mail\s*:|$)", contact, re.I)
    mailto = next((clean(a.get("href", ""))[7:].split("?", 1)[0] for a in soup.select('a[href^="mailto:"]')), "")
    return {"房东姓名": landlord or UNKNOWN, "房东电话": clean(phone_match.group(1)) if phone_match else UNKNOWN,
            "房东手机": clean(mobile_match.group(1)) if mobile_match else UNKNOWN,
            "房东邮箱": mailto or clean(fields.get("E-Mail") or UNKNOWN)}


def parse_detail(detail):
    soup = BeautifulSoup(detail.get("html") or "", "html.parser")
    address = section_html(soup, "Standort")
    full_text = clean(detail.get("full_text"))
    match = re.search(r"Zimmertyp:\s*(.+?)\s+Zu vermieten:", full_text)
    room_type = clean(match.group(1)) if match else ""
    fields = dict(detail.get("fields") or {})
    equipment = parse_equipment(soup)
    entry_match = re.search(r"(?:ANGEBOT|EINTRAG)\s+VOM\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})", clean(detail.get("title")) + " " + full_text, re.I)
    structured = {
        "Eintrag vom": entry_match.group(1) if entry_match else UNKNOWN,
        "地址": address, "房型/面积": room_type,
        "可入住时间": clean(fields.get("Zu vermieten") or UNKNOWN),
        "家具": clean(fields.get("Einrichtung") or UNKNOWN), "备注": clean(fields.get("Bemerkung") or ""),
    }
    structured.update(parse_costs(fields, full_text, equipment))
    structured.update(equipment)
    structured.update(parse_landlord(fields, full_text, soup))
    for label in ("嵌入式厨房", "灶台", "冰箱", "洗碗机", "洗衣机", "烘干机", "接受男性", "接受女性", "接受情侣", "接受非吸烟者"):
        structured.setdefault(label, UNKNOWN)
    structured.update({"title": detail.get("title", ""), "full_text": full_text})
    return {
        "address": address, "room_type_text": room_type,
        "rental_type": classify_room(room_type), "detail": structured,
    }


def postcode(value):
    match = re.search(r"\b(\d{5})\b", clean(value))
    return match.group(1) if match else ""


def candidates(address):
    values = [(address, "完整地址")]
    normalized = re.sub(r"\s*-\s*(?:UG|EG|OG|DG)\s+(?=\d{5}\b)", " ", address, flags=re.I)
    left = normalized.split(" - ", 1)[0].strip()
    left = re.sub(r",?\s*Apart\.-Nr\.\s*\S+", "", left, flags=re.I).strip(" ,")
    match = re.match(r"^(.*?)\s+(\d{5})\s+(.+)$", left)
    if match:
        street, postal, city = (clean(value) for value in match.groups())
        city = re.sub(r"-(?:Bahnhof|Zentrum)$", "", city, flags=re.I).strip()
        if street and not re.search(r"(?:zweifamilienhaus|wohnung|apartment)", street, re.I):
            values.extend([
                (f"{street}, {postal} {city}, Deutschland", "街道/城市"),
                (f"{street}, {postal}, Deutschland", "街道/邮编"),
            ])
        values.append((f"{postal} {city}, Deutschland", "邮编/城市近似位置"))
    result, seen = [], set()
    for query, accuracy in values:
        if query not in seen:
            result.append((query, accuracy))
            seen.add(query)
    return result


def geocode(address, session):
    cached, result = get_geocode_cache(address)
    if cached:
        return result, True
    expected = postcode(address)
    result = None
    for attempt, (query, accuracy) in enumerate(candidates(address)):
        if attempt:
            time.sleep(GEOCODE_DELAY_SECONDS)
        response = session.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "de", "addressdetails": 1},
            headers={"User-Agent": USER_AGENT}, timeout=30,
        )
        response.raise_for_status()
        for item in response.json():
            returned = clean((item.get("address") or {}).get("postcode")) or postcode(item.get("display_name"))
            if expected and returned != expected:
                continue
            result = {
                "lat": float(item["lat"]), "lon": float(item["lon"]),
                "display_name": item.get("display_name", ""), "postcode": returned,
                "accuracy": accuracy, "query": query,
            }
            break
        if result:
            break
    put_geocode_cache(address, result)
    return result, False


def geocode_pending(progress=None):
    progress = progress or (lambda *_args, **_kwargs: None)
    pending = rooms_pending_geocode()
    stats = {"total": len(pending), "located": 0, "failed": 0, "cached": 0}
    if not pending:
        return stats
    session = requests.Session()
    session.trust_env = False
    session.proxies.update({"http": GEOCODE_PROXY, "https": GEOCODE_PROXY})
    last_uncached = False
    for index, room in enumerate(pending, 1):
        cached, _ = get_geocode_cache(room["address"])
        if last_uncached and not cached:
            time.sleep(GEOCODE_DELAY_SECONDS)
        try:
            result, was_cached = geocode(room["address"], session)
            last_uncached = not was_cached
            stats["cached"] += int(was_cached)
            stats["located"] += int(bool(result))
            stats["failed"] += int(not result)
            save_room_coordinates(room["id"], result)
            progress(
                "geocode",
                f"定位 {index}/{len(pending)}：ID {room['id']} · {'缓存' if was_cached else '新查询'} · {'成功' if result else '未定位'}",
                geocode_index=index, geocode_total=len(pending), room_id=room["id"],
            )
        except Exception as exc:
            stats["failed"] += 1
            progress(
                "geocode_error", f"定位阶段暂停：ID {room['id']} · {exc}",
                geocode_index=index, geocode_total=len(pending), room_id=room["id"],
            )
            break
    return stats

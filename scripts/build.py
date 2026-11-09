"""Classify every street block around Midtown / Georgia Tech and build the map.

Input : data/raw/*.geojson (from fetch_data.py) + data/sources/paid_sources.json
Output: data/parking.geojson   standard GeoJSON, one feature per street block
        dist/index.html        the single-file map (web/template.html + data)

Statuses
  free     no meter, permit rule, or parking ban on record  -> likely free
  limited  free with a posted time limit (permit streets: visitors get the limit)
  night    restricted by day, free 7 PM - 7 AM
  check    busy road, unconfirmed listing, or meter-district side street -> read the signs
  paid     metered / paid (city meters lift on Sundays, holidays, and outside
           7 AM - 10 PM; web/hours.js turns these "free now" in the page)
  campus   Georgia Tech permit / paid visitor parking
  none     no street parking (highways, ramps, park drives, major arterials)

The rules are applied in a fixed order (see `build`): hard exclusions first,
then block-level evidence, then district-level inference, then the default.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SOURCES = ROOT / "data" / "sources" / "paid_sources.json"
WEB = ROOT / "web"
TEMPLATE = WEB / "template.html"
OUT_GEOJSON = ROOT / "data" / "parking.geojson"
OUT_HTML = ROOT / "dist" / "index.html"

HIGHWAY_CLASSES = {"Interstate Principal Arterial", "Exit Ramp", "Entrance Ramp", "Freeway and Expressway"}
HIGHWAY_PREFIXES = ("downtown conn", "interstate", "northside 75", "buford hwy", "spring pine conn")
# Multi-lane roads the city classes as collectors/arterials but that have no curb parking.
NO_PARKING_ROADS = {"northside dr nw", "tech pkwy nw"}

# Block listing rules (data/sources/paid_sources.json -> listings)
RULE_RANK = {"meter": 5, "night": 4, "check": 3, "free_limit": 2, "free": 1, "unknown": 0}
LISTING_STATUS = {"meter": "paid", "night": "night", "check": "check", "free_limit": "limited", "free": "free"}
LISTING_REASON = {
    "meter": "Confirmed meter on this block",
    "night": "Restricted by day, free in the evening",
    "check": "Restricted at certain hours",
    "free_limit": "Free with a posted time limit",
    "free": "Listed as free all day",
}
DROP_CLASSES = {"Parking", "Alley"}  # parking-lot aisles and alleys are not streets
FOCUS_HOODS = {"Midtown", "Georgia Tech", "Home Park", "Atlantic Station", "Marietta Street Artery",
               "Ansley Park", "Loring Heights"}

# ---------------------------------------------------------------- geometry

M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(33.78))


def dist_m(a, b) -> float:
    return math.hypot((a[0] - b[0]) * M_PER_DEG_LON, (a[1] - b[1]) * M_PER_DEG_LAT)


def line_parts(geom) -> list[list[list[float]]]:
    if not geom:
        return []
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def length_m(coords) -> float:
    return sum(dist_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def midpoint(coords) -> list[float]:
    half = length_m(coords) / 2
    run = 0.0
    for i in range(len(coords) - 1):
        d = dist_m(coords[i], coords[i + 1])
        if run + d >= half and d > 0:
            t = (half - run) / d
            return [coords[i][0] + t * (coords[i + 1][0] - coords[i][0]),
                    coords[i][1] + t * (coords[i + 1][1] - coords[i][1])]
        run += d
    return coords[len(coords) // 2]


def point_seg_dist(p, a, b) -> float:
    ax, ay = a[0] * M_PER_DEG_LON, a[1] * M_PER_DEG_LAT
    bx, by = b[0] * M_PER_DEG_LON, b[1] * M_PER_DEG_LAT
    px, py = p[0] * M_PER_DEG_LON, p[1] * M_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_line_dist(p, coords) -> float:
    return min(point_seg_dist(p, coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def polygons(geom) -> list[list[list[list[float]]]]:
    if not geom:
        return []
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def in_ring(p, ring) -> bool:
    x, y = p
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_geom(p, geom) -> bool:
    for poly in polygons(geom):
        if poly and in_ring(p, poly[0]) and not any(in_ring(p, h) for h in poly[1:]):
            return True
    return False


def simplify(coords, tol_m: float):
    """Douglas-Peucker."""
    if len(coords) < 3:
        return coords
    a, b = coords[0], coords[-1]
    idx, dmax = 0, 0.0
    for i in range(1, len(coords) - 1):
        d = point_seg_dist(coords[i], a, b)
        if d > dmax:
            idx, dmax = i, d
    if dmax <= tol_m:
        return [a, b]
    return simplify(coords[: idx + 1], tol_m)[:-1] + simplify(coords[idx:], tol_m)


def label_point(geom) -> list[float]:
    """Interior point farthest from the edge (grid search): label anchors that
    stay inside L-shaped or skinny neighborhoods, unlike a vertex average."""
    rings = [r for poly in polygons(geom) for r in poly[:1]]
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    best, best_d = [sum(xs) / len(xs), sum(ys) / len(ys)], -1.0
    n = 40
    for i in range(1, n):
        for j in range(1, n):
            p = [min(xs) + (max(xs) - min(xs)) * i / n, min(ys) + (max(ys) - min(ys)) * j / n]
            if not in_geom(p, geom):
                continue
            d = min(point_line_dist(p, r) for r in rings)
            if d > best_d:
                best, best_d = p, d
    return best


def rnd(coords, nd=5):
    return [[round(x, nd), round(y, nd)] for x, y in coords]


# ---------------------------------------------------------------- names

ABBR = {
    "street": "st", "avenue": "ave", "drive": "dr", "road": "rd", "place": "pl", "boulevard": "blvd",
    "circle": "cir", "lane": "ln", "parkway": "pkwy", "terrace": "ter", "court": "ct", "way": "way",
    "walk": "walk", "trail": "trl", "highway": "hwy",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
    "west": "w", "north": "n", "east": "e", "south": "s",
}
ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
            "sixth": "6th", "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th",
            "eleventh": "11th", "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th",
            "fifteenth": "15th", "sixteenth": "16th", "seventeenth": "17th", "eighteenth": "18th"}


def norm(name: str | None) -> str:
    if not name:
        return ""
    toks = re.sub(r"[.,]", " ", name.lower()).split()
    return " ".join(ORDINALS.get(t, ABBR.get(t, t)) for t in toks)


def parse_address(addr: str) -> tuple[int, str]:
    num, street = addr.split(" ", 1)
    return int(num), norm(street)


# ---------------------------------------------------------------- OSM tags

YES = {"lane", "parallel", "diagonal", "perpendicular", "yes", "street_side", "half_on_kerb", "on_kerb", "shoulder"}
NO = {"no", "no_parking", "no_stopping", "separate", "fire_lane"}


def osm_verdict(tags: dict) -> str | None:
    sides = defaultdict(set)
    fee = set()
    for k, v in tags.items():
        if not k.startswith("parking"):
            continue
        parts = k.split(":")
        if parts[-1] == "fee" or k.endswith(":fee"):
            fee.add(v)
            continue
        if parts[-1] in {"orientation", "surface", "markings", "restriction", "access", "maxstay", "zone"}:
            continue
        side = next((s for s in ("both", "left", "right") if s in parts), None)
        if side:
            sides[side].add(v)
    vals = set().union(*sides.values()) if sides else set()
    if not vals:
        return None
    if vals <= NO:
        return "none"
    if vals & YES:
        if "yes" in fee:
            return "paid"
        return "lane"
    return None


# ---------------------------------------------------------------- main

def load(name):
    return json.loads((RAW / name).read_text())


def build() -> dict:
    """Classify every street block and build the basemap layers. Reads the
    inputs and returns the result; writes nothing."""
    src = json.loads(SOURCES.read_text())
    centerlines = load("centerlines.geojson")["features"]
    hoods = load("neighborhoods.geojson")["features"]
    areas = load("osm_areas.geojson")["features"]
    osm_tagged = load("osm_parking_tags.geojson")["features"]

    hood_index = []
    for f in hoods:
        geom = f["geometry"]
        xs = [p[0] for poly in polygons(geom) for p in poly[0]]
        ys = [p[1] for poly in polygons(geom) for p in poly[0]]
        hood_index.append((f["properties"]["NAME"], geom, (min(xs), min(ys), max(xs), max(ys))))

    def hood_of(p):
        for name, geom, (x0, y0, x1, y1) in hood_index:
            if x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and in_geom(p, geom):
                return name
        return None

    parks = [f for f in areas if f["properties"].get("leisure") == "park"]

    def in_park(p):
        return next((f["properties"].get("name", "a park") for f in parks if in_geom(p, f["geometry"])), None)

    # Piedmont Ave as a lat -> lon function; the Midtown meter district stops there.
    piedmont = sorted(
        (pt for f in centerlines if f["properties"].get("LABEL") == "Piedmont Ave NE"
         for part in line_parts(f["geometry"]) for pt in part),
        key=lambda q: q[1])

    def west_of_piedmont(p) -> bool:
        best = min(piedmont, key=lambda q: abs(q[1] - p[1]))
        return p[0] < best[0] - 0.0002  # ~20 m margin so Piedmont itself is excluded

    # Block listings -> segment ids. When several listings land on one block,
    # the most restrictive rule wins.
    listing_hits: dict[int, dict] = {}
    unmatched = []
    by_name = defaultdict(list)
    for f in centerlines:
        by_name[norm(f["properties"].get("LABEL"))].append(f)
    for item in src["listings"]["items"]:
        num, street = parse_address(item["addr"])
        # Gap between the address and each segment's range (0 = inside). Some
        # city segments carry one-sided ranges, so allow the nearest range
        # within 40 house numbers.
        scored = []
        for f in by_name.get(street, []):
            p = f["properties"]
            rng = [v for v in (p["FROMLEFT"], p["TOLEFT"], p["FROMRIGHT"], p["TORIGHT"]) if v]
            if rng:
                lo, hi = min(rng), max(rng)
                scored.append((0 if lo <= num <= hi else min(abs(num - lo), abs(num - hi)), f))
        best = min((g for g, _ in scored), default=None)
        if best is None or best > 40:
            unmatched.append(item["addr"])
            continue
        for g, f in scored:
            if g != best:
                continue
            oid = f["properties"]["OBJECTID"]
            cur = listing_hits.get(oid)
            if cur is None or RULE_RANK[item["rule"]] > RULE_RANK[cur["rule"]]:
                listing_hits[oid] = item

    # Midpoints of listed-meter blocks, per street, for the "next to a meter" check.
    meter_mids = defaultdict(list)
    for f in centerlines:
        hit = listing_hits.get(f["properties"]["OBJECTID"])
        if hit and hit["rule"] == "meter":
            for part in line_parts(f["geometry"]):
                meter_mids[norm(f["properties"]["LABEL"])].append(midpoint(part))

    # Street rules, with optional cross-street bounds.
    def crossing_lat(street: str, cross: str) -> float | None:
        a = [pt for f in by_name.get(norm(street), []) for part in line_parts(f["geometry"]) for pt in part]
        b = [pt for f in by_name.get(norm(cross), []) for part in line_parts(f["geometry"]) for pt in part]
        if not a or not b:
            return None
        pa, pb = min(((x, y) for x in a for y in b), key=lambda t: dist_m(t[0], t[1]))
        return (pa[1] + pb[1]) / 2

    street_rules = defaultdict(list)
    for r in src["street_rules"]["items"]:
        r = dict(r)
        if "between" in r:
            lats = [crossing_lat(r["street"], c) for c in r["between"]]
            if None in lats:
                raise RuntimeError(f"can't place {r['street']} between {r['between']}")
            r["lat_range"] = (min(lats) - 0.0002, max(lats) + 0.0002)
        street_rules[norm(r["street"])].append(r)

    def street_rule(n, hood, mid):
        for r in street_rules.get(n, []):
            if r.get("hood") and r["hood"] != hood:
                continue
            if r.get("east_of_piedmont") and west_of_piedmont(mid):
                continue
            if "lat_range" in r and not (r["lat_range"][0] <= mid[1] <= r["lat_range"][1]):
                continue
            return r
        return None

    # OSM parking verdicts, matched by name + proximity
    osm_lines = []
    for f in osm_tagged:
        v = osm_verdict(f["properties"])
        if v and f["geometry"]["type"] == "LineString":
            osm_lines.append((norm(f["properties"].get("name")), v, f["geometry"]["coordinates"], f["id"]))

    corridors = src["metered_corridors"]
    midtown_streets = {norm(s) for s in corridors["Midtown"]["streets"]}
    districts = {k: v for k, v in src["districts"].items() if not k.startswith("_")}

    out = []
    stats = Counter()
    km = Counter()
    hood_km = defaultdict(Counter)
    for f in centerlines:
        p = f["properties"]
        fc = p.get("FUNCCLASS")
        label = p.get("LABEL")
        if fc in DROP_CLASSES or not label or label == "None":
            stats["dropped (lot aisle / alley / unnamed)"] += 1
            continue
        for coords in line_parts(f["geometry"]):
            if len(coords) < 2:
                continue
            mid = midpoint(coords)
            seg_len = length_m(coords)
            hood = hood_of(mid)
            n = norm(label)
            osm = None
            osm_id = None
            for oname, v, oc, oid in osm_lines:
                if oname == n and point_line_dist(mid, oc) < 25:
                    osm, osm_id = v, oid
                    break

            status, reason, source, timed = "free", "No meter, permit rule or parking ban on record", None, False
            conf, detail, until = "", "", None
            park = in_park(mid)
            district = districts.get(hood or "")
            listing = listing_hits.get(p["OBJECTID"])
            srule = street_rule(n, hood, mid)
            if fc in HIGHWAY_CLASSES or n.startswith(HIGHWAY_PREFIXES):
                status, reason = "none", "Highway or ramp"
            elif n in NO_PARKING_ROADS and hood != "Downtown":
                status, reason = "none", "Multi-lane road with no curb parking"
            elif park:
                status, reason = "none", f"Drive inside {park}"
            elif osm == "none":
                status, reason, source = "none", "Mapped as no parking", f"https://www.openstreetmap.org/{osm_id}"
            elif listing and listing["rule"] != "unknown":
                status, reason = LISTING_STATUS[listing["rule"]], LISTING_REASON[listing["rule"]]
                detail, source, conf = listing["detail"], listing["url"], "listing"
                if listing["rule"] == "meter":
                    if hood == "Atlantic Station":
                        reason = "Paid street parking, enforced every day (Atlantic Station)"
                    else:
                        timed, until = True, listing.get("until")
            elif osm == "paid":
                status, reason, timed, conf = "paid", "Mapped as paid parking", True, "osm"
                source = f"https://www.openstreetmap.org/{osm_id}"
            elif district and district["kind"] == "campus":
                status, reason, source = "campus", district["reason"], district["source"]
            elif district and district["kind"] == "private":
                status, reason, source, conf = "paid", district["reason"], district["source"], "district"
            elif hood == "Midtown" and n in midtown_streets and west_of_piedmont(mid):
                status, reason, timed, conf = "paid", "Inside the Midtown meter district", True, "district"
                source = corridors["source"]
            elif hood == "Downtown" and corridors["Downtown"].get("all_streets"):
                status, reason, timed, conf = "paid", "Inside the Downtown core meter district", True, "district"
                source = corridors["source"]
            elif fc == "Principal Arterial":
                status, reason = "none", "Major arterial: curb parking generally not allowed"
            elif srule:
                status = "limited" if srule["rule"] == "limited" else "check"
                reason, source = srule["reason"], srule["source"]
            elif listing:  # a listing exists but its rule isn't confirmed
                status, reason = "check", "On-street listing whose rules aren't confirmed"
                detail, source = listing["detail"], listing["url"]
            elif fc == "Minor Arterial":
                status, reason = "check", "Busy road: curb parking is often banned or limited to off-peak hours"
            elif district and district["kind"] == "limited":
                status, reason, source = "limited", district["reason"], district["source"]
            elif district and district["kind"] == "permit":
                status, reason, source = "check", district["reason"], district["source"]
            elif any(dist_m(mid, q) < 150 for q in meter_mids.get(n, [])):
                status, reason = "check", "Same street as a confirmed meter one block over; may be metered too"
                source = src["listings"]["source"]
            elif hood == "Midtown" and west_of_piedmont(mid):
                status, reason = "check", "Side street inside the Midtown meter district; may be metered"
                source = corridors["source"]
            elif osm == "lane":
                reason = "Parking lane mapped, no fee on record"
                source = f"https://www.openstreetmap.org/{osm_id}"
            elif district and district["kind"] == "mapped":
                reason, source = district["reason"], district["source"]
            elif hood == "Midtown":
                reason = "Residential Midtown: no meter or permit rule on record for this block"

            rng = [v for v in (p["FROMLEFT"], p["TOLEFT"], p["FROMRIGHT"], p["TORIGHT"]) if v]
            block = f"{min(rng)}–{max(rng)}" if rng else ""
            props = {"id": p["OBJECTID"], "name": label, "block": block, "status": status,
                     "reason": reason, "hood": hood or "", "timed": timed,
                     "len": round(seg_len), "class": fc or ""}
            for key, val in (("source", source), ("conf", conf), ("detail", detail), ("until", until)):
                if val:
                    props[key] = val
            out.append({"type": "Feature", "properties": props,
                        "geometry": {"type": "LineString", "coordinates": rnd(coords)}})
            stats[status] += 1
            km[status] += seg_len / 1000
            hood_km[hood or "(none)"][status] += seg_len / 1000

    # ------------------------------------------------ basemap layers
    hood_out = []
    for f in hoods:
        name = f["properties"]["NAME"]
        polys = []
        for poly in polygons(f["geometry"]):
            ring = simplify(poly[0], 6)
            if len(ring) >= 4:
                polys.append([rnd(ring)])
        lp = label_point(f["geometry"]) if name in FOCUS_HOODS else None
        hood_out.append({"type": "Feature",
                         "properties": {"name": name, "focus": name in FOCUS_HOODS,
                                        "label": [round(lp[1], 5), round(lp[0], 5)] if lp else None,
                                        "district": districts.get(name, {}).get("kind", "")},
                         "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    park_out = []
    for f in parks:
        polys = []
        for poly in polygons(f["geometry"]):
            ring = simplify(poly[0], 4)
            if len(ring) >= 4:
                polys.append([rnd(ring)])
        if polys:
            park_out.append({"type": "Feature", "properties": {"name": f["properties"].get("name", "")},
                             "geometry": {"type": "MultiPolygon", "coordinates": polys}})

    return {
        "src": src,
        "streets": {"type": "FeatureCollection", "features": out},
        "hoods": {"type": "FeatureCollection", "features": hood_out},
        "parks": {"type": "FeatureCollection", "features": park_out},
        "stats": stats,
        "km": km,
        "hood_km": hood_km,
        "unmatched": unmatched,
    }


DESCRIPTION = ("Free street parking around Midtown Atlanta, Georgia Tech, Home Park and the Westside, "
               "by day and hour, with the evidence behind every block.")
# The green "P" sign, inline so browsers don't request /favicon.ico.
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
           "%3Crect x='1' y='1' width='30' height='30' rx='6' fill='%2312804A'/%3E"
           "%3Ctext x='16' y='23.5' font-family='Arial,Helvetica,sans-serif' font-weight='700' "
           "font-size='21' text-anchor='middle' fill='%23fff'%3EP%3C/text%3E%3C/svg%3E")


def full_document(fragment: str) -> str:
    """Wrap the page fragment in a standalone document, moving its <title> into
    <head> next to the description, link-preview tags and favicon."""
    m = re.search(r"<title>(.*?)</title>\n?", fragment)
    title = m.group(1) if m else "Free Curb ATL"
    body = fragment.replace(m.group(0), "", 1) if m else fragment
    head = (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"<title>{title}</title>\n"
        f'<meta name="description" content="{DESCRIPTION}">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{DESCRIPTION}">\n'
        '<meta property="og:type" content="website">\n'
        f'<link rel="icon" href="{FAVICON}">\n'
        "<style>body{margin:0}[hidden]{display:none!important}</style>\n"
    )
    return f'<!doctype html>\n<html lang="en">\n<head>\n{head}</head>\n<body>\n{body}\n</body>\n</html>\n'


def write_outputs(result: dict) -> None:
    """Write data/parking.geojson and, when the web template is present, the map page."""
    src = result["src"]
    OUT_GEOJSON.write_text(json.dumps(result["streets"]))
    print(f"wrote {OUT_GEOJSON.relative_to(ROOT)}  ({len(result['streets']['features'])} blocks)")
    if not TEMPLATE.exists():
        return
    bundle = {
        "compiled": src["compiled"],
        "enforcement": src["enforcement"],
        "streets": result["streets"],
        "hoods": result["hoods"],
        "parks": result["parks"],
    }
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.read_text()
    # Artifact pages may only load stylesheets from Google Fonts, so the
    # Leaflet CSS ships inline.
    html = html.replace("/*__LEAFLET_CSS__*/", (WEB / "vendor" / "leaflet.css").read_text())
    html = html.replace("/*__HOURS_JS__*/", (WEB / "hours.js").read_text())
    html = html.replace("/*__DATA__*/null", json.dumps(bundle, separators=(",", ":")))
    # artifact.html: page fragment (claude.ai wraps it in a document at publish).
    # index.html: the same page as a full document, for self-hosting and GitHub Pages.
    (OUT_HTML.parent / "artifact.html").write_text(html)
    OUT_HTML.write_text(full_document(html))
    print(f"wrote {OUT_HTML.relative_to(ROOT)} + artifact.html  ({OUT_HTML.stat().st_size / 1024:.0f} KB)")


def report(result: dict) -> None:
    """Print coverage numbers so a data change's effect is visible at a glance."""
    src, stats, km, hood_km, unmatched = (result[k] for k in ("src", "stats", "km", "hood_km", "unmatched"))
    out = result["streets"]["features"]
    print("\nblocks by status:", dict(stats))
    print("km by status:", {k: round(v, 1) for k, v in km.items()})
    print("\nkm by neighborhood (focus areas):")
    for h in sorted(hood_km, key=lambda h: -sum(hood_km[h].values())):
        if h in FOCUS_HOODS or h == "Downtown":
            print(f"  {h:24s}", {k: round(v, 1) for k, v in sorted(hood_km[h].items())})
    paid_conf = Counter()
    for f in out:
        if f["properties"]["status"] == "paid":
            paid_conf[f["properties"].get("conf", "")] += f["properties"]["len"] / 1000
    print("paid km by confidence:", {k: round(v, 1) for k, v in paid_conf.items()})
    print("\nlistings matched:", len(src["listings"]["items"]) - len(unmatched),
          "of", len(src["listings"]["items"]))
    if unmatched:
        print("  unmatched:", unmatched)


def main() -> None:
    result = build()
    write_outputs(result)
    report(result)


if __name__ == "__main__":
    main()

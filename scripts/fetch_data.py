"""Download the raw inputs for the Midtown / Georgia Tech free-parking map.

Sources
- City of Atlanta street centerlines (official, with address ranges and
  functional class): gis.atlantaga.gov OpenDataService1/FeatureServer/33
- City of Atlanta neighborhood boundaries: GeopoliticalArea/FeatureServer/1
- OpenStreetMap (Overpass): street-parking tags, the Georgia Tech campus
  outline, and parks.

Writes GeoJSON into data/raw/. Re-run any time to refresh.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "atl-free-parking/0.1 (personal project)"}

# south, west, north, east: North Ave-ish to Atlantic Station / Ansley Park,
# Marietta St / Howell Mill to Piedmont Park.
BBOX = (33.763, -84.420, 33.802, -84.364)

CITY = "https://gis.atlantaga.gov/dpcd/rest/services"
CENTERLINE_FIELDS = [
    "OBJECTID", "PREDIR", "PRETYPE", "NAME", "POSTTYPE", "POSTDIR", "LABEL",
    "FROMLEFT", "TOLEFT", "FROMRIGHT", "TORIGHT", "FUNCCLASS", "SYMBOLCLASS",
    "OWNERSHIP", "BRIDGE", "STATUS", "DIVIDED", "LANES", "DIRTRAVEL",
    "SPEEDLIMIT", "ZIPLEFT", "ZIPRIGHT",
]


def http_json(url: str, data: bytes | None = None, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def arcgis_geojson(layer_url: str, fields: list[str] | str) -> dict:
    """Fetch every feature intersecting BBOX, paging by object id (offset paging
    silently truncates on this server)."""
    s, w, n, e = BBOX
    out_fields = fields if isinstance(fields, str) else ",".join(fields)
    spatial = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    ids = http_json(f"{layer_url}/query?" + urllib.parse.urlencode(
        {**spatial, "returnIdsOnly": "true", "f": "json"}))["objectIds"] or []
    ids.sort()
    feats: list = []
    for i in range(0, len(ids), 200):
        chunk = ",".join(str(x) for x in ids[i:i + 200])
        body = urllib.parse.urlencode({
            "objectIds": chunk, "outFields": out_fields, "outSR": "4326", "f": "geojson",
        }).encode()
        feats.extend(http_json(f"{layer_url}/query", data=body).get("features", []))
        time.sleep(0.3)
    if len(feats) != len(ids):
        raise RuntimeError(f"{layer_url}: expected {len(ids)} features, got {len(feats)}")
    return {"type": "FeatureCollection", "features": feats}


def overpass(query: str) -> dict:
    # curl rather than urllib: the macOS system Python's TLS stack fails the
    # handshake with overpass-api.de.
    out = subprocess.run(
        ["curl", "-s", "-m", "240", "-A", UA["User-Agent"], "--data-urlencode",
         f"data={query}", "https://overpass-api.de/api/interpreter"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def osm_to_geojson(osm: dict, as_polygon: bool) -> dict:
    """Minimal OSM->GeoJSON for ways and simple multipolygon relations (out geom)."""
    feats = []
    for el in osm.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "way" and "geometry" in el:
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if as_polygon and len(coords) >= 4 and coords[0] == coords[-1]:
                geom = {"type": "Polygon", "coordinates": [coords]}
            else:
                geom = {"type": "LineString", "coordinates": coords}
        elif el["type"] == "relation" and as_polygon:
            outers = [
                [[p["lon"], p["lat"]] for p in m["geometry"]]
                for m in el.get("members", [])
                if m.get("role") == "outer" and "geometry" in m
            ]
            rings = _stitch(outers)
            if not rings:
                continue
            geom = {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
        else:
            continue
        feats.append({"type": "Feature", "id": f"{el['type']}/{el['id']}",
                      "properties": tags, "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def _stitch(parts: list[list[list[float]]]) -> list[list[list[float]]]:
    """Join open way segments of a relation into closed rings."""
    parts = [p[:] for p in parts if len(p) >= 2]
    rings = []
    while parts:
        ring = parts.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, p in enumerate(parts):
                if p[0] == ring[-1]:
                    ring += p[1:]
                elif p[-1] == ring[-1]:
                    ring += p[::-1][1:]
                elif p[-1] == ring[0]:
                    ring = p[:-1] + ring
                elif p[0] == ring[0]:
                    ring = p[::-1][:-1] + ring
                else:
                    continue
                parts.pop(i)
                changed = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def save(name: str, obj: dict) -> None:
    path = RAW / name
    path.write_text(json.dumps(obj))
    print(f"wrote {path.relative_to(ROOT)}  ({len(obj.get('features', []))} features)")


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    s, w, n, e = BBOX
    bb = f"{s},{w},{n},{e}"

    save("centerlines.geojson", arcgis_geojson(
        f"{CITY}/OpenDataService1/FeatureServer/33", CENTERLINE_FIELDS))
    save("neighborhoods.geojson", arcgis_geojson(
        f"{CITY}/AdministrativeArea/GeopoliticalArea/FeatureServer/1", "*"))

    save("osm_parking_tags.geojson", osm_to_geojson(overpass(
        f'[out:json][timeout:120];way["highway"]["highway"!~"footway|path|steps|cycleway|pedestrian|corridor"]'
        f'[~"^parking"~"."]({bb});out geom;'), as_polygon=False))
    time.sleep(2)
    save("osm_areas.geojson", osm_to_geojson(overpass(
        f'[out:json][timeout:120];('
        f'wr["amenity"="university"]({bb});'
        f'wr["leisure"="park"]({bb});'
        f'wr["landuse"="cemetery"]({bb});'
        f');out geom;'), as_polygon=True))


if __name__ == "__main__":
    main()

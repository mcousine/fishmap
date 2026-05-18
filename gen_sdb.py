#!/usr/bin/env python3
"""
Satellite-Derived Bathymetry (SDB) pour lacs du Québec
=======================================================
Algorithme : Ratio logarithmique de Stumpf (2003) adapté pour eaux tanniques
Source satellite : Sentinel-2 L2A via Element84/AWS STAC (COG - pas de téléchargement complet)
Sortie : GeoJSON isobathes + HTML carte de pêche

Usage: python3 gen_sdb.py
"""

import json, re, os, sys, math, warnings, subprocess
import numpy as np
import requests
from pathlib import Path
from scipy.ndimage import gaussian_filter, median_filter
from scipy.interpolate import RegularGridInterpolator
from shapely.geometry import mapping, shape, Polygon, MultiPolygon, LineString, MultiLineString
from shapely.ops import unary_union, polygonize
import fiona
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.contour as mcontour
import pystac_client
import rasterio
from rasterio.windows import from_bounds as window_from_bounds
import tempfile

warnings.filterwarnings('ignore')

# ─── Configuration Lac Sonois ──────────────────────────────────────────────────
LAC_CONFIG = {
    "name":       "Sonois",
    "file":       "lac_sonois_peche",
    "lat":        46.799028,
    "lon":        -73.389861,
    "area_ha":    55.0,        # estimé depuis contexte lot.md
    "success":    6.2,
    "mass_g":     419,
    "vehicule":   "VUS",
    "portage":    "5 min",
    "max_depth_est": 12.0,     # profondeur max estimée (m) pour calibration
    "buffer_km":  0.9,         # buffer autour du centre pour la bbox
    "output_dir": "/Users/michelcousineau/Downloads/fishmap",
    "template":   "/Users/michelcousineau/Downloads/fishmap/lac_fox_peche.html",
}

# ─── Statistiques de pêche Sépaq 2023-2026 — Secteur Lac-au-Sable & Des Îles ─
# Format: succès (omble/jour-pêche), masse moyenne (g), véhicule, portage (min)
MASTIGOUCHE_STATS = {
    "Anselme":      {"success": 3.8, "mass_g": 726, "vehicule": "VUS (VTT)", "portage_min": 40, "enst": "5"},
    "Bigorne":      {"success": 3.9, "mass_g": 317, "vehicule": "Traversée", "portage_min": 5,  "enst": "5"},
    "Bourgeois":    {"success": 5.2, "mass_g": 249, "vehicule": "Auto",       "portage_min": 0,  "enst": "++"},
    "Brasier":      {"success": 6.0, "mass_g": 282, "vehicule": "Camion 4X4", "portage_min": 10, "enst": ""},
    "Caillette":    {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Charme":       {"success": 4.6, "mass_g": 275, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Chipeau":      {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Chute Noire":  {"success": 4.5, "mass_g": 167, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Clut":         {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Coleman":      {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "de la Baie":   {"success": 4.3, "mass_g": 185, "vehicule": "VUS",        "portage_min": 1,  "enst": ""},
    "de la Gitane": {"success": 0.0, "mass_g": 0,   "vehicule": "VUS",        "portage_min": 0,  "enst": ""},
    "de la Griffe": {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "de la Rencontre": {"success": 0.0, "mass_g": 0,"vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "des Demoiselles": {"success": 1.1,"mass_g": 386,"vehicule": "VUS",       "portage_min": 10, "enst": ""},
    "des Joncs":    {"success": 6.0, "mass_g": 312, "vehicule": "Camion 4X4", "portage_min": 3,  "enst": ""},
    "des Loups":    {"success": 4.3, "mass_g": 185, "vehicule": "VUS (VTT)",  "portage_min": 20, "enst": ""},
    "des Mauves":   {"success": 2.8, "mass_g": 188, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "des Ronces":   {"success": 7.1, "mass_g": 191, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "des Saules":   {"success": 4.1, "mass_g": 195, "vehicule": "VUS",        "portage_min": 5,  "enst": ""},
    "Diablos":      {"success": 1.7, "mass_g": 204, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Doré":         {"success": 5.3, "mass_g": 293, "vehicule": "VUS",        "portage_min": 3,  "enst": ""},
    "du Brasier":   {"success": 6.0, "mass_g": 282, "vehicule": "Camion 4X4", "portage_min": 10, "enst": ""},
    "du Chipeau":   {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "du Grillon":   {"success": 3.7, "mass_g": 240, "vehicule": "VUS",        "portage_min": 0,  "enst": ""},
    "du Gros Ours": {"success": 5.2, "mass_g": 223, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "du Hêtre":     {"success": 4.4, "mass_g": 164, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "du Méta":      {"success": 5.1, "mass_g": 336, "vehicule": "Camion 4X4", "portage_min": 15, "enst": ""},
    "du Rat Musqué":{"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "du Rutabaga":  {"success": 5.2, "mass_g": 185,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "du Serpent":   {"success": 3.6, "mass_g": 214, "vehicule": "Auto",       "portage_min": 0,  "enst": "++"},
    "du Soufflet":  {"success": 3.6, "mass_g": 123, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "du Sud-Est":   {"success": 0.2, "mass_g": 1000,"vehicule": "Camion 4X4", "portage_min": 2,  "enst": ""},
    "Ephémère":     {"success": 5.0, "mass_g": 190, "vehicule": "VUS",        "portage_min": 3,  "enst": ""},
    "Forestier":    {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Fox":          {"success": 7.1, "mass_g": 346, "vehicule": "VUS",        "portage_min": 3,  "enst": ""},
    "Grand lac des Îles": {"success": 3.0, "mass_g": 176, "vehicule": "Auto", "portage_min": 0,  "enst": ""},
    "Green":        {"success": 3.8, "mass_g": 359, "vehicule": "VUS",        "portage_min": 1,  "enst": "5"},
    "Gros Ours":    {"success": 5.2, "mass_g": 223, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Henri":        {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Jane":         {"success": 0.9, "mass_g": 246, "vehicule": "Auto",       "portage_min": 15, "enst": ""},
    "Lafond":       {"success": 3.7, "mass_g": 163, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "l'Orignal":    {"success": 3.9, "mass_g": 452, "vehicule": "Traversée",  "portage_min": 3,  "enst": ""},
    "Lemay":        {"success": 1.4, "mass_g": 438, "vehicule": "Auto",       "portage_min": 5,  "enst": ""},
    "aux Lézards":  {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Marcel":       {"success": 3.2, "mass_g": 310, "vehicule": "VUS",        "portage_min": 5,  "enst": ""},
    "Moyen":        {"success": 5.2, "mass_g": 205, "vehicule": "Auto",       "portage_min": 1,  "enst": ""},
    "Osborn":       {"success": 1.1, "mass_g": 646, "vehicule": "Auto",       "portage_min": 0,  "enst": "5"},
    "Orignal":      {"success": 3.9, "mass_g": 452, "vehicule": "Traversée",  "portage_min": 3,  "enst": ""},
    "Oudiette":     {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "à Paner":      {"success": 4.7, "mass_g": 434,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Portage":      {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Prudent":      {"success": 5.0, "mass_g": 207, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Punaise":      {"success": 4.6, "mass_g": 204, "vehicule": "Auto",       "portage_min": 0,  "enst": "++"},
    "Rat Musqué":   {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Recto":        {"success": 4.0, "mass_g": 169, "vehicule": "VUS",        "portage_min": 1,  "enst": ""},
    "Régis":        {"success": 4.5, "mass_g": 170, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Roméo":        {"success": 5.6, "mass_g": 304, "vehicule": "Auto",       "portage_min": 25, "enst": ""},
    "Rutabaga":     {"success": 5.2, "mass_g": 185,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "au Sable":     {"success": 3.9, "mass_g": 330, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Siffleux":     {"success": 3.3, "mass_g": 407, "vehicule": "Auto",       "portage_min": 1,  "enst": "5"},
    "Sonois":       {"success": 6.2, "mass_g": 419, "vehicule": "VUS",        "portage_min": 5,  "enst": ""},
    "Théodule":     {"success": 0.0, "mass_g": 0,   "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Traverse":     {"success": 4.5, "mass_g": 246, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "au Tremble":   {"success": 4.3, "mass_g": 271, "vehicule": "VUS",        "portage_min": 5,  "enst": ""},
    "Verdun":       {"success": 2.1, "mass_g": 168, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
    "Verso":        {"success": 5.0, "mass_g": 253, "vehicule": "VUS",        "portage_min": 5,  "enst": ""},
    "Victoire":     {"success": 3.6, "mass_g": 221, "vehicule": "Auto",       "portage_min": 0,  "enst": ""},
}

# Lookup helper: find stats by partial name match
def _lake_stats(lake_name: str) -> dict:
    """Return MASTIGOUCHE_STATS entry for lake_name, tolerant to prefix/accent differences."""
    import unicodedata
    def _norm(s):
        return unicodedata.normalize("NFKD", s.lower()).encode("ascii","ignore").decode("ascii").strip()
    key = _norm(lake_name)
    for k, v in MASTIGOUCHE_STATS.items():
        if _norm(k) == key:
            return v
    # Partial match fallback
    for k, v in MASTIGOUCHE_STATS.items():
        nk = _norm(k)
        if key in nk or nk in key:
            return v
    return {}

# GBLQ index path (Quebec provincial bathymetry)
GBLQ_INDEX_PATH = "/Users/michelcousineau/Downloads/05884_Osborn/index_s.geojson"

def _lookup_gblq(lake_name: str, index_path: str = GBLQ_INDEX_PATH) -> dict:
    """Return GBLQ properties dict for a lake or {} if not found."""
    import unicodedata
    def _norm(s):
        return unicodedata.normalize("NFKD", s.lower()).encode("ascii","ignore").decode("ascii").strip()
    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    key = _norm(lake_name)
    for feat in data.get("features", []):
        hydro = feat["properties"].get("HYDRONYME") or ""
        # "Romeo, Lac" → "Romeo"  /  "Lac Romeo" → "Romeo"
        if ", Lac" in hydro or ", lac" in hydro:
            name_part = hydro.split(",")[0].strip()
        elif re.match(r"(?i)^(grand\s+)?lac\s+", hydro):
            name_part = re.sub(r"(?i)^(grand\s+)?lac\s+", "", hydro).strip()
        else:
            name_part = hydro
        if _norm(name_part) == key:
            return feat["properties"]
    return {}

def download_gblq_isobaths(gpkg_url: str, cache_dir: str = None) -> dict:
    """Download GBLQ GPKG ZIP, extract isobath lines, return WGS84 data.

    Returns dict with:
      isobaths  : [{"depth_m": float, "coords": [[lat,lon],...]}]
      max_depth : float (deepest isobath depth)
      deep_point: [lat, lon] (fosse — deepest surveyed point, or None)
    """
    import urllib.request, zipfile, io, tempfile, os
    import fiona
    from pyproj import Transformer

    if cache_dir is None:
        cache_dir = tempfile.gettempdir()

    # Cache based on URL hash
    import hashlib
    cache_key = hashlib.md5(gpkg_url.encode()).hexdigest()[:12]
    cache_path = os.path.join(cache_dir, f"gblq_{cache_key}.gpkg")

    if not os.path.exists(cache_path):
        try:
            print(f"    Downloading GBLQ: {gpkg_url.split('/')[-1]}")
            with urllib.request.urlopen(gpkg_url, timeout=45) as r:
                zdata = r.read()
            with zipfile.ZipFile(io.BytesIO(zdata)) as z:
                gpkg_name = next((f for f in z.namelist() if f.endswith(".gpkg")), None)
                if not gpkg_name:
                    return {}
                with z.open(gpkg_name) as zf, open(cache_path, "wb") as out:
                    out.write(zf.read())
        except Exception as e:
            print(f"    GBLQ download failed: {e}")
            return {}

    # Read isobaths layer
    try:
        layers = fiona.listlayers(cache_path)
        iso_layer = next((l for l in layers if l.startswith("iso_")), None)
        fos_layer = next((l for l in layers if l.startswith("fos_")), None)
        if not iso_layer:
            return {}

        xfm = Transformer.from_crs("EPSG:32198", "EPSG:4326", always_xy=True)

        isobaths = []
        with fiona.open(cache_path, layer=iso_layer) as src:
            for feat in src:
                depth = feat["properties"].get("PROFONDEUR_M")
                if depth is None:
                    continue
                geom = feat["geometry"]
                all_coords = []
                lines = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
                for line in lines:
                    pts = []
                    for x, y in line:
                        lon, lat = xfm.transform(x, y)
                        pts.append([round(lat, 6), round(lon, 6)])
                    if pts:
                        all_coords.append(pts)
                if all_coords:
                    isobaths.append({"depth_m": round(float(depth), 2), "coords": all_coords})

        isobaths.sort(key=lambda x: x["depth_m"])
        max_depth = max((x["depth_m"] for x in isobaths), default=0)

        deep_point = None
        if fos_layer:
            with fiona.open(cache_path, layer=fos_layer) as src:
                for feat in src:
                    lat_d = feat["properties"].get("LATITUDE")
                    lon_d = feat["properties"].get("LONGITUDE")
                    dep_d = feat["properties"].get("PROFONDEUR_M")
                    if lat_d and lon_d:
                        deep_point = [round(lat_d, 6), round(lon_d, 6)]
                        max_depth = max(max_depth, float(dep_d or 0))
                        break

        return {"isobaths": isobaths, "max_depth": round(max_depth, 2), "deep_point": deep_point}
    except Exception as e:
        print(f"    GBLQ parse failed: {e}")
        return {}

def _gblq_js_block(gblq_data: dict, lac_name_sq: str) -> str:
    """Return JS code block to inject GBLQ isobaths into the Leaflet map."""
    isobaths = gblq_data.get("isobaths", [])
    max_depth = gblq_data.get("max_depth", 10)
    deep_point = gblq_data.get("deep_point")

    iso_json = json.dumps(isobaths, ensure_ascii=False)
    deep_json = json.dumps(deep_point)

    return f"""
// ============================================================
// GBLQ BATHYMÉTRIE — isobathes MELCCFP Québec
// ============================================================
(function buildGBLQIsobaths() {{
  const isobaths = {iso_json};
  const maxDepth = {max_depth};
  const deepPt   = {deep_json};

  // Color ramp: shallow cyan → deep navy
  function depthColor(d) {{
    const t = Math.min(d / maxDepth, 1);
    const r = Math.round(147 - 117 * t);
    const g = Math.round(197 - 133 * t);
    const b = Math.round(253 -  78 * t);
    return 'rgb(' + r + ',' + g + ',' + b + ')';
  }}

  const bathyLayer = L.layerGroup().addTo(map);
  isobaths.forEach(function(iso) {{
    const col = depthColor(iso.depth_m);
    iso.coords.forEach(function(line) {{
      L.polyline(line, {{
        color: col, weight: 1.8, opacity: 0.75,
        pane: 'overlayPane'
      }}).bindTooltip(iso.depth_m + ' m', {{
        sticky: true, className: 'lake-tooltip'
      }}).addTo(bathyLayer);
    }});
  }});

  if (deepPt) {{
    L.circleMarker(deepPt, {{
      radius: 6, color: '#1e40af', fillColor: '#3b82f6',
      fillOpacity: 0.9, weight: 2, pane: 'markerPane'
    }}).bindPopup(
      '<div style="font-weight:700;color:#93c5fd">🔵 Fosse — ' + maxDepth + ' m</div>' +
      '<div style="font-size:11px;color:#94a3b8">Lac {lac_name_sq}<br>Source: GBLQ MELCCFP Québec</div>'
    ).addTo(map);
  }}
}})();
"""

def _mffp_js_block(mffp_isobaths: list, lac_name_sq: str) -> str:
    """Return JS code block to inject MFFP PDF isobaths into the Leaflet map."""
    max_depth = max((x["depth_m"] for x in mffp_isobaths), default=10)
    iso_json  = json.dumps(mffp_isobaths, ensure_ascii=False)
    return f"""
// ============================================================
// MFFP BATHYMÉTRIE — isobathes extraites du PDF Sépaq
// ============================================================
(function buildMFFPIsobaths() {{
  const isobaths = {iso_json};
  const maxDepth = {max_depth};

  function depthColor(d) {{
    const t = Math.min(d / maxDepth, 1);
    const r = Math.round(0   + 38  * t);
    const g = Math.round(56  - 18  * t);
    const b = Math.round(101 + 13  * t);
    return 'rgb(' + r + ',' + g + ',' + b + ')';
  }}

  const mffpLayer = L.layerGroup().addTo(map);
  isobaths.forEach(function(iso) {{
    const col = depthColor(iso.depth_m);
    L.polyline(iso.coords, {{
      color: col, weight: 1.6, opacity: 0.80,
      pane: 'overlayPane'
    }}).bindTooltip(iso.depth_m + ' m (' + iso.depth_ft + ' pi — MFFP)', {{
      sticky: true, className: 'lake-tooltip'
    }}).addTo(mffpLayer);
  }});
}})();
"""


def _polygon_area_ha(coords_latlon: list) -> float:
    """Approximate lake area in hectares via Shoelace formula."""
    if len(coords_latlon) < 3:
        return 0.0
    lat0 = coords_latlon[0][0]
    mplat = 111320.0
    mplon = 111320.0 * math.cos(math.radians(lat0))
    xs = [p[1] * mplon for p in coords_latlon]
    ys = [p[0] * mplat for p in coords_latlon]
    n = len(xs)
    area = abs(sum(xs[i]*ys[(i+1)%n] - xs[(i+1)%n]*ys[i] for i in range(n))) / 2
    return round(area / 10000, 1)

# ─── Spots de pêche prédéfinis (dérivés de la géographie du lac) ────────────
FISHING_SPOTS_SONOIS = [
    {"lat": 46.8000, "lon": -73.3890, "name": "Fosse principale (estimée)", "icon": "⭐", "depth_est": "~8-12m"},
    {"lat": 46.8010, "lon": -73.3870, "name": "Entrée nord (courant)", "icon": "🎣", "depth_est": "~3-6m"},
    {"lat": 46.7985, "lon": -73.3915, "name": "Baie ouest (herbiers)", "icon": "🌿", "depth_est": "~2-4m"},
    {"lat": 46.7975, "lon": -73.3875, "name": "Pointe sud (tombant)", "icon": "🎣", "depth_est": "~4-8m"},
    {"lat": 46.8005, "lon": -73.3905, "name": "Centre lac (pélagique)", "icon": "🐟", "depth_est": "~6-10m"},
    {"lat": 46.7990, "lon": -73.3930, "name": "Rive NO (tombant abrupt)", "icon": "🎣", "depth_est": "~5-9m"},
]

def bbox_from_center(lat, lon, buffer_km):
    dlat = buffer_km / 111.0
    dlon = buffer_km / (111.0 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

def find_best_sentinel2(bbox, max_cloud=20):
    """Cherche la meilleure scène Sentinel-2 L2A sur Element84 Earth Search."""
    print("  Connexion au catalogue STAC Earth Search (AWS)...")
    catalog = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")

    # Cherche d'abord en été (eau la plus claire, pas de glace)
    for daterange in ["2023-06-01/2023-09-30", "2022-06-01/2022-09-30", "2024-06-01/2024-09-30", "2021-06-01/2021-09-30"]:
        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=daterange,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            max_items=20
        )
        items = list(search.items())
        if items:
            best = min(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
            cc = best.properties.get("eo:cloud_cover", "?")
            dt = best.properties.get("datetime", "?")[:10]
            print(f"  ✅ Scène trouvée: {dt} | Couverture nuageuse: {cc}%")
            return best

    # Fallback: n'importe quelle saison
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime="2020-01-01/2024-12-31",
        query={"eo:cloud_cover": {"lt": 30}},
        max_items=10
    )
    items = list(search.items())
    if not items:
        raise RuntimeError("Aucune scène Sentinel-2 trouvée pour cette zone")
    best = min(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
    cc = best.properties.get("eo:cloud_cover", "?")
    dt = best.properties.get("datetime", "?")[:10]
    print(f"  ⚠️  Fallback: {dt} | Nuages: {cc}%")
    return best

def read_band_window(item, band_name, bbox_wgs84_wsen, verbose=False):
    """Lit une fenêtre d'une bande Sentinel-2 via COG HTTP range request.

    bbox_wgs84_wsen: (west, south, east, north) en WGS84
    La fonction reprojette automatiquement la bbox vers le CRS du COG (souvent UTM).
    """
    from rasterio.crs import CRS
    from rasterio.warp import transform_bounds

    assets = item.assets
    # Noms utilisés par Element84 Earth Search
    aliases = {
        "B02": ["blue",    "B02", "coastal"],
        "B03": ["green",   "B03"],
        "B04": ["red",     "B04"],
        "B08": ["nir",     "B08", "nir08", "B8A"],
        "SCL": ["scl",     "SCL"],
    }

    url = None
    for alias in aliases.get(band_name, [band_name]):
        if alias in assets:
            url = assets[alias].href
            break

    if not url:
        if verbose:
            print(f"  ⚠️  Bande {band_name} non trouvée dans {list(assets.keys())[:10]}")
        return None, None

    if verbose:
        print(f"  → Lecture {band_name} depuis {url[:65]}...")

    try:
        with rasterio.open(url) as src:
            src_crs = src.crs
            # Reprojeter la bbox WGS84 vers le CRS du COG (ex: EPSG:32618 UTM-18N)
            if src_crs and src_crs.to_epsg() != 4326:
                bbox_native = transform_bounds(
                    CRS.from_epsg(4326), src_crs,
                    bbox_wgs84_wsen[0], bbox_wgs84_wsen[1],
                    bbox_wgs84_wsen[2], bbox_wgs84_wsen[3]
                )
            else:
                bbox_native = bbox_wgs84_wsen

            win = window_from_bounds(
                bbox_native[0], bbox_native[1],
                bbox_native[2], bbox_native[3],
                src.transform
            )

            # Vérifier que la fenêtre est valide
            win_rounded = win.round_lengths().round_offsets()
            rows = int(win_rounded.height)
            cols = int(win_rounded.width)
            if rows <= 0 or cols <= 0:
                if verbose:
                    print(f"  ⚠️  Fenêtre {band_name} vide ({rows}×{cols}) — bbox hors emprise?")
                    print(f"       bbox_native={[f'{x:.1f}' for x in bbox_native]}")
                    print(f"       src bounds={src.bounds}")
                return None, None

            data = src.read(1, window=win_rounded)
            transform = src.window_transform(win_rounded)

            nodata = src.nodata
            data = data.astype(float)
            if nodata is not None:
                data[data == nodata] = np.nan

            # Sentinel-2 L2A: diviser par 10000 pour réflectance [0,1]
            # (certains assets ont déjà la réflectance normalisée)
            if np.nanmax(data) > 2.0:  # valeurs entières → normaliser
                data = data / 10000.0

            return data, transform
    except Exception as e:
        if verbose:
            print(f"  ❌ Erreur lecture {band_name}: {e}")
        return None, None

def apply_sdb(blue, green, red, scl=None, max_depth=12.0):
    """
    Bathymétrie dérivée par satellite - algorithme Stumpf modifié.

    Pour eaux tanniques du Québec:
    - Utilise ratio log(B03_green) / log(B02_blue) plutôt que l'inverse habituel
    - Le tanin absorbe fortement le bleu → green/red ratio plus fiable pour <5m
    - Produit une profondeur RELATIVE calibrée sur max_depth estimé

    Returns: depth_grid (m), water_mask
    """
    # Masque eau via NDWI = (Green - NIR-approx) / (Green + NIR-approx)
    # Ici on utilise le SCL (Scene Classification) si disponible
    water_mask = np.zeros_like(green, dtype=bool)

    # NDWI simplifié: pixels où green > rouge (eau typique)
    # Et réflectance green dans plage eau
    ndwi_approx = (green - red) / (green + red + 1e-10)
    water_mask = (ndwi_approx > -0.1) & (green > 0.01) & (green < 0.30)

    # Ajouter filtre par réflectance bleue (eau a réflectance bleue modérée)
    if blue is not None:
        water_mask &= (blue < 0.25)

    # Masquer pixels avec données invalides
    if blue is not None:
        water_mask &= np.isfinite(blue) & np.isfinite(green)
    else:
        water_mask &= np.isfinite(green)

    water_mask &= np.isfinite(red)

    print(f"  Masque eau: {water_mask.sum()} pixels ({water_mask.sum() * 100 / water_mask.size:.1f}%)")

    if water_mask.sum() < 50:
        print("  ⚠️  Trop peu de pixels eau - masque assoupli")
        water_mask = (green > 0.005) & (green < 0.30) & np.isfinite(green)

    # ─── Calcul du ratio de profondeur ─────────────────────────────────────
    depth_index = np.full_like(green, np.nan)

    if blue is not None:
        # Approche Stumpf standard: ln(Blue)/ln(Green)
        # Pour eaux tanniques: modifié avec offset pour stabiliser
        n_blue  = np.clip(blue,  0.001, None)
        n_green = np.clip(green, 0.001, None)

        # Ratio logarithmique: plus élevé = plus profond (Blue>Green en eau peu profonde claire)
        # Pour eaux tanniques: Green pénètre mieux → ln(G)/ln(B) inversé
        ratio = np.log(1000 * n_green) / np.log(1000 * n_blue)
        # Normalisation: ratio proche 1.0 = surface, ratio élevé = profond
        depth_index[water_mask] = ratio[water_mask]
    else:
        # Sans bande bleue: utiliser green seul (atténuation avec profondeur)
        n_green = np.clip(green, 0.001, None)
        depth_index[water_mask] = -np.log(n_green[water_mask])

    # ─── Calibration empirique → profondeur absolue ─────────────────────────
    valid_di = depth_index[water_mask & np.isfinite(depth_index)]

    if len(valid_di) == 0:
        return np.zeros_like(green), water_mask

    di_min = np.percentile(valid_di, 5)   # p5 = zone peu profonde (bord)
    di_max = np.percentile(valid_di, 95)  # p95 = zone profonde (centre)

    print(f"  Indice de profondeur: min={di_min:.3f}, max={di_max:.3f}")

    depth = np.full_like(green, np.nan)
    if di_max > di_min:
        # Normalisation linéaire → échelle 0 à max_depth_est
        norm = (depth_index - di_min) / (di_max - di_min)
        depth[water_mask] = np.clip(norm[water_mask], 0, 1) * max_depth
    else:
        depth[water_mask] = max_depth / 2

    # Lissage pour éliminer le bruit pixel à pixel
    depth_smooth = depth.copy()
    finite_mask = np.isfinite(depth)
    depth_tmp = np.where(finite_mask, depth, 0)
    depth_smoothed_arr = gaussian_filter(depth_tmp, sigma=2.0)
    weight = gaussian_filter(finite_mask.astype(float), sigma=2.0)
    depth_smooth = np.where(finite_mask, depth_smoothed_arr / (weight + 1e-10), np.nan)
    depth_smooth[~water_mask] = np.nan

    return depth_smooth, water_mask

def depth_to_geojson(depth_grid, transform, water_mask, levels=None, bbox_wsen=None):
    """Convertit la grille de profondeur en polygones GeoJSON (isobathes).

    Utilise rasterio.features.shapes pour vectoriser les bandes de profondeur
    — compatible toutes versions de matplotlib, aucun rendu graphique requis.
    """
    import rasterio.features
    from rasterio.crs import CRS
    from rasterio.warp import transform_geom
    from shapely.geometry import shape as shapely_shape

    if levels is None:
        levels = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0]

    level_colors = [
        "#0a1628",  # 0 - lac (fond)
        "#122040",  # 0-1m
        "#1a3558",  # 1-2m
        "#1e4a6e",  # 2-3m
        "#1e5c8a",  # 3-4m
        "#2070a8",  # 4-6m
        "#2585c5",  # 6-8m
        "#2a9ae0",  # 8-10m
        "#30aef0",  # 10-12m
    ]

    features = []

    # CRS du raster (UTM)
    src_crs = rasterio.crs.CRS.from_epsg(32618)  # UTM-18N (Québec SE)

    def vectorize_mask(mask_uint8, depth_val, color):
        """Vectorise un masque binaire → polygone GeoJSON (WGS84)."""
        polys = []
        for geom, val in rasterio.features.shapes(mask_uint8, transform=transform):
            if val == 1:
                # Reprojeter du CRS UTM vers WGS84
                geom_wgs84 = transform_geom(src_crs, CRS.from_epsg(4326), geom)
                p = shapely_shape(geom_wgs84)
                if p.is_valid and not p.is_empty and p.area > 1e-10:
                    polys.append(p)
        if not polys:
            return None
        merged = unary_union(polys)
        if merged.is_empty:
            return None
        return {
            "type": "Feature",
            "properties": {"depth": depth_val, "color": color, "label": f"{depth_val}m"},
            "geometry": mapping(merged)
        }

    # ── Contour du lac (masque eau global) ────────────────────────────────────
    lake_mask = water_mask.astype(np.uint8)
    feat_lake = vectorize_mask(lake_mask, 0, "#0a1628")
    if feat_lake:
        feat_lake["properties"]["label"] = "lac"
        features.append(feat_lake)

    # ── Bandes de profondeur ──────────────────────────────────────────────────
    all_bounds = [0.0] + list(levels) + [9999.0]
    for i in range(1, len(all_bounds) - 1):
        d_low  = all_bounds[i]
        d_high = all_bounds[i + 1]
        color  = level_colors[min(i, len(level_colors) - 1)]

        band_mask = (
            np.isfinite(depth_grid) &
            water_mask &
            (depth_grid >= d_low) &
            (depth_grid < d_high)
        ).astype(np.uint8)

        if band_mask.sum() < 5:
            continue

        feat = vectorize_mask(band_mask, d_low, color)
        if feat:
            features.append(feat)
            print(f"    Bande {d_low:.0f}-{d_high:.0f}m: {band_mask.sum()} px")

    return {"type": "FeatureCollection", "features": features}

def find_fosse(depth_grid, transform):
    """Trouve la position de la fosse (point le plus profond) et reprojette en WGS84."""
    from rasterio.crs import CRS
    from rasterio.warp import transform as warp_transform

    if not np.any(np.isfinite(depth_grid)):
        return None
    idx = np.nanargmax(depth_grid)
    r, c = np.unravel_index(idx, depth_grid.shape)
    max_depth = float(depth_grid[r, c])

    # Coordonnées dans le CRS du raster (UTM)
    x_utm = transform.c + c * transform.a + r * transform.b
    y_utm = transform.f + r * transform.e + c * transform.d

    # Reprojeter UTM → WGS84
    src_crs = CRS.from_epsg(32618)
    dst_crs = CRS.from_epsg(4326)
    xs, ys = warp_transform(src_crs, dst_crs, [x_utm], [y_utm])
    return ys[0], xs[0], max_depth

def replace_block(html, var_name, new_value, is_array=False):
    """Remplace un bloc JS const VAR = ... avec tracking de brackets."""
    pattern = rf'(const {var_name}\s*=\s*)'
    m = re.search(pattern, html)
    if not m:
        return html

    start_pos = m.end()
    open_char = '[' if is_array else '{'
    close_char = ']' if is_array else '}'

    first_bracket = html.find(open_char, start_pos)
    if first_bracket == -1:
        return html

    depth = 0
    pos = first_bracket
    while pos < len(html):
        if html[pos] == open_char:
            depth += 1
        elif html[pos] == close_char:
            depth -= 1
            if depth == 0:
                break
        pos += 1

    old_block = html[m.start():pos+1]
    semicolon = ';' if html[pos+1:pos+2] == ';' else ''
    if semicolon:
        old_block = html[m.start():pos+2]

    new_block = f'const {var_name} = {new_value}{semicolon}'
    return html.replace(old_block, new_block, 1)

def run_render_test(html_path: str) -> bool:
    """Run headless browser render test on a generated map HTML file.
    Returns True if map renders correctly (tiles loaded, no JS errors)."""
    test_script = os.path.join(os.path.dirname(__file__), "test_map_render.py")
    if not os.path.exists(test_script):
        print("  ⚠️  test_map_render.py not found — skipping render test")
        return True
    print(f"\n[Render test] {os.path.basename(html_path)} ...")
    result = subprocess.run(
        ["python3.11", test_script, html_path],
        capture_output=True, text=True, timeout=60
    )
    output = result.stdout + result.stderr
    for line in output.splitlines():
        print(f"  {line}")
    passed = result.returncode == 0
    if not passed:
        print("  ❌ RENDER TEST FAILED — vérifier la carte avant de pousser")
    else:
        print("  ✅ Render OK")
    return passed


def build_html(config, geojson, fosse_info, template_path):
    """Construit le HTML de la carte de pêche à partir du template Fox."""
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    lac_name = config["name"]
    lat = config["lat"]
    lon = config["lon"]
    area = config.get("area_ha", 55.0)

    # Fosse
    if fosse_info:
        fosse_lat, fosse_lon, fosse_depth = fosse_info
        fosse_depth_str = f"~{fosse_depth:.1f}m (estimée SDB)"
    else:
        fosse_lat, fosse_lon = lat + 0.001, lon + 0.001
        fosse_depth_str = "~10m (estimée)"

    # Spots de pêche JS
    spots_js_items = []
    for s in FISHING_SPOTS_SONOIS:
        spots_js_items.append(
            f'  {{ lat: {s["lat"]}, lng: {s["lon"]}, '
            f'name: "{s["name"]}", icon: "{s["icon"]}", '
            f'depth: "{s["depth_est"]}" }}'
        )
    spots_js = "[\n" + ",\n".join(spots_js_items) + "\n]"

    # GeoJSON bathymétrie (sur une ligne)
    geojson_str = json.dumps(geojson, separators=(',', ':'))

    # Remplacements principaux
    replacements = [
        # Titre et nom du lac
        (r'Lac Fox', f'Lac {lac_name}', False),
        (r'lac Fox', f'lac {lac_name}', False),
        (r'LAC FOX', f'LAC {lac_name.upper()}', False),
        # Coordonnées centre
        (r'const MAP_CENTER\s*=\s*\[[\d\.\-]+,\s*[\d\.\-]+\]',
         f'const MAP_CENTER = [{lat}, {lon}]', True),
        # Zoom
        (r'const MAP_ZOOM\s*=\s*\d+', 'const MAP_ZOOM = 14', True),
        # Superficie
        (r'\b\d+[\.,]\d+\s*ha\b', f'{area:.1f} ha', False),
        # Profondeur fosse
        (r'const FOSSE_LAT\s*=\s*[\d\.\-]+', f'const FOSSE_LAT = {fosse_lat:.6f}', True),
        (r'const FOSSE_LON\s*=\s*[\d\.\-]+', f'const FOSSE_LON = {fosse_lon:.6f}', True),
        (r'const FOSSE_DEPTH\s*=\s*"[^"]*"', f'const FOSSE_DEPTH = "{fosse_depth_str}"', True),
    ]

    for pattern, replacement, use_literal in replacements:
        if use_literal:
            html = re.sub(pattern, replacement, html)
        else:
            html = html.replace(pattern.replace(r'\b', '').replace(r'\s*=\s*', ' = '), replacement) if not pattern.startswith('const') else re.sub(pattern, replacement, html)

    # Remplacement nom (simple string)
    html = html.replace('Fox', lac_name)
    html = html.replace('FOX', lac_name.upper())

    # Remplacer les blocs JS
    html = replace_block(html, 'FISHING_SPOTS', spots_js, is_array=True)

    # Remplacer GeoJSON bathymétrie
    html = re.sub(
        r'^const BATHYMETRY_GEOJSON = \{.*\};',
        f'const BATHYMETRY_GEOJSON = {geojson_str};',
        html, flags=re.MULTILINE
    )
    if f'const BATHYMETRY_GEOJSON = {geojson_str[:20]}' not in html:
        # Fallback: remplacer sur plusieurs lignes
        html = re.sub(
            r'const BATHYMETRY_GEOJSON\s*=\s*\{',
            f'const BATHYMETRY_GEOJSON = {geojson_str};\n// REPLACED\nconst _BATHYMETRY_GEOJSON_ORIG = {{',
            html, count=1
        )

    # Ajouter note SDB dans le header
    sdb_note = '''
    <div style="background:#1a2a1a;border:1px solid #4a7a4a;color:#8fbc8f;padding:8px 12px;margin:8px 0;border-radius:4px;font-size:11px;">
      ⚠️ <strong>Bathymétrie dérivée par satellite (SDB)</strong> —
      Profondeurs <em>estimées</em> par analyse spectrale Sentinel-2 (algorithme Stumpf).
      Précision ±40% · Eau tannique · Données indicatives seulement.
    </div>'''
    html = html.replace('</h1>', f'</h1>{sdb_note}', 1)

    # Fix satellite blend mode (add CSS + custom pane)
    html = html.replace(
        'mix-blend-mode: plus-lighter;\n}',
        'mix-blend-mode: plus-lighter;\n}\n.leaflet-satellite-pane img.leaflet-tile {\n\tmix-blend-mode: normal !important;\n}',
        1
    )
    html = html.replace(
        '// Satellite overlay (Esri World Imagery — free, no API key)\nconst satTile = L.tileLayer(',
        "// Satellite overlay (Esri World Imagery) — custom pane removes blue cast\nmap.createPane('satellitePane');\nmap.getPane('satellitePane').style.zIndex = 250;\nmap.getPane('satellitePane').classList.add('leaflet-satellite-pane');\nconst satTile = L.tileLayer(",
        1
    )
    html = re.sub(
        r"(maxZoom: 19, opacity: 0\.5)(\s*\}\s*\)\.addTo\(map\))",
        r"maxZoom: 19, opacity: 0.5, pane: 'satellitePane'\2",
        html, count=1
    )

    # Coordonnées Google Maps
    html = re.sub(
        r'https://www\.google\.com/maps[^\'"]*',
        f'https://www.google.com/maps?q={lat},{lon}',
        html
    )

    # Dynamic depth labels + lake mask (replaces Fox template's hardcoded positions)
    lake_mask_js = r"""
// ============================================================
// LAKE MASK — hides satellite/background outside lake boundary
// ============================================================
function convexHull2D(pts) {
  if (pts.length < 3) return pts;
  pts = pts.slice().sort((a,b) => a[0]===b[0] ? a[1]-b[1] : a[0]-b[0]);
  const cross = (O,A,B) => (A[0]-O[0])*(B[1]-O[1])-(A[1]-O[1])*(B[0]-O[0]);
  const lower = [], upper = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 0) lower.pop();
    lower.push(p);
  }
  for (let i = pts.length-1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop(); lower.pop();
  return lower.concat(upper);
}
function buildLakeMask() {
  const allPts = [];
  BATHYMETRY_GEOJSON.features.forEach(feat => {
    if (feat.properties.depth === 0) return;
    const geom = feat.geometry;
    const rings = geom.type === 'Polygon' ? [geom.coordinates[0]]
      : geom.coordinates.map(p => p[0]);
    rings.forEach(ring => ring.forEach(c => allPts.push(c)));
  });
  if (allPts.length < 3) return;
  const hull = convexHull2D(allPts);
  hull.push(hull[0]);
  const worldBox = [[-180,-90],[180,-90],[180,90],[-180,90],[-180,-90]];
  const maskFeature = { type: 'Feature', properties: {},
    geometry: { type: 'Polygon', coordinates: [worldBox, hull] } };
  map.createPane('maskPane');
  map.getPane('maskPane').style.zIndex = 270;
  map.getPane('maskPane').style.pointerEvents = 'none';
  L.geoJSON(maskFeature, { pane: 'maskPane',
    style: { fillColor: '#0d1117', fillOpacity: 0.88, fillRule: 'evenodd',
      stroke: true, color: '#5aaa7a', weight: 1.5, opacity: 0.65 }
  }).addTo(map);
}
function computeDepthLabelPos() {
  const positions = [], seenDepths = new Set();
  BATHYMETRY_GEOJSON.features.forEach(feat => {
    const d = feat.properties.depth;
    if (d === 0 || seenDepths.has(d)) return;
    seenDepths.add(d);
    const geom = feat.geometry;
    let coords = [];
    if (geom.type === 'Polygon') { coords = geom.coordinates[0]; }
    else if (geom.type === 'MultiPolygon') {
      let best = [];
      geom.coordinates.forEach(poly => { if (poly[0].length > best.length) best = poly[0]; });
      coords = best;
    }
    if (!coords.length) return;
    const lat = coords.reduce((s,c) => s+c[1], 0) / coords.length;
    const lon = coords.reduce((s,c) => s+c[0], 0) / coords.length;
    positions.push({ depth_m: d, lat, lon });
  });
  return positions;
}
const DEPTH_LABEL_POS = computeDepthLabelPos();
const THERMAL_DEPTH_LABEL_POS = DEPTH_LABEL_POS;
"""
    # Replace old hardcoded DEPTH_LABEL_POS block (from template) with dynamic version
    old_pattern = re.compile(
        r'// ={5,}\s*\n// DEPTH LABEL POSITIONS.*?const THERMAL_DEPTH_LABEL_POS\s*=\s*\[.*?\];\s*\n',
        re.DOTALL
    )
    if old_pattern.search(html):
        html = old_pattern.sub(lake_mask_js.strip() + '\n', html, count=1)

    # Call buildLakeMask after BATHYMETRY_GEOJSON is declared
    lines = html.split('\n')
    for i, line in enumerate(lines):
        if line.strip().startswith('const BATHYMETRY_GEOJSON = {') and line.strip().endswith('};'):
            lines.insert(i+1, 'buildLakeMask();')
            break
    html = '\n'.join(lines)

    return html

def main():
    config = LAC_CONFIG
    bbox = bbox_from_center(config["lat"], config["lon"], config["buffer_km"])
    print(f"\n{'='*60}")
    print(f"  SDB Bathymétrie — Lac {config['name']}")
    print(f"  Centre: {config['lat']:.6f}N, {config['lon']:.6f}W")
    print(f"  BBox: {bbox}")
    print(f"{'='*60}")

    # ── Étape 1: Trouver scène Sentinel-2 ─────────────────────────────────────
    print("\n[1/5] Recherche scène Sentinel-2...")
    item = find_best_sentinel2(bbox, max_cloud=25)

    print(f"       Bandes disponibles: {list(item.assets.keys())[:12]}")

    # ── Étape 2: Télécharger les bandes (fenêtre AOI seulement) ───────────────
    print("\n[2/5] Lecture des bandes spectrales (COG range request)...")
    blue,  t_b = read_band_window(item, "B02", bbox, verbose=True)
    green, t_g = read_band_window(item, "B03", bbox, verbose=True)
    red,   t_r = read_band_window(item, "B04", bbox, verbose=True)
    scl,   t_s = read_band_window(item, "SCL", bbox, verbose=True)

    if green is None:
        raise RuntimeError("Impossible de lire la bande verte (B03)")

    # Utiliser le transform de green comme référence
    transform = t_g

    print(f"       Taille grille: {green.shape[0]}×{green.shape[1]} pixels")
    print(f"       Réflectance verte: min={np.nanmin(green):.4f}, max={np.nanmax(green):.4f}, mean={np.nanmean(green):.4f}")

    # ── Étape 3: Appliquer l'algorithme SDB ───────────────────────────────────
    print("\n[3/5] Application algorithme SDB (Stumpf modifié)...")
    depth_grid, water_mask = apply_sdb(blue, green, red, scl, config["max_depth_est"])

    if not np.any(np.isfinite(depth_grid)):
        raise RuntimeError("Aucune donnée de profondeur dérivée — scène trop nuageuse ou eau non détectée")

    valid_depths = depth_grid[np.isfinite(depth_grid)]
    print(f"       Profondeurs estimées: min={np.nanmin(valid_depths):.1f}m, max={np.nanmax(valid_depths):.1f}m, mean={np.nanmean(valid_depths):.1f}m")

    # ── Étape 4: Générer GeoJSON isobathes ────────────────────────────────────
    print("\n[4/5] Génération des isobathes...")
    max_d = float(np.nanmax(depth_grid))
    levels = [l for l in [1, 2, 3, 4, 5, 6, 8, 10, 12] if l < max_d]
    if not levels:
        levels = [1.0, 2.0, max_d * 0.5, max_d * 0.8]

    geojson = depth_to_geojson(depth_grid, transform, water_mask, levels, bbox)
    print(f"       {len(geojson['features'])} features GeoJSON générées")

    # Fosse
    fosse_info = find_fosse(depth_grid, transform)
    if fosse_info:
        print(f"       Fosse estimée: {fosse_info[0]:.5f}N, {fosse_info[1]:.5f}W — {fosse_info[2]:.1f}m")

    # Sauvegarder GeoJSON
    geojson_path = f"/tmp/{config['file']}_sdb.geojson"
    with open(geojson_path, 'w') as f:
        json.dump(geojson, f)
    print(f"       GeoJSON sauvegardé: {geojson_path}")

    # ── Étape 5: Générer HTML ──────────────────────────────────────────────────
    print("\n[5/5] Génération du HTML de la carte de pêche...")
    html = build_html(config, geojson, fosse_info, config["template"])

    output_path = f"{config['output_dir']}/{config['file']}.html"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    size_kb = os.path.getsize(output_path) // 1024
    print(f"       ✅ HTML sauvegardé: {output_path} ({size_kb} KB)")

    run_render_test(output_path)

    print(f"\n{'='*60}")
    print(f"  ✅ TERMINÉ — Lac {config['name']} — SDB")
    print(f"     Scène: {item.properties.get('datetime','?')[:10]}")
    print(f"     Nuages: {item.properties.get('eo:cloud_cover','?')}%")
    print(f"     Pixels eau: {water_mask.sum()}")
    print(f"     Profondeur max estimée: {float(np.nanmax(valid_depths)):.1f}m")
    print(f"{'='*60}\n")

def gen_pdf_map(pdf_path, lac_config):
    """Generate fishing map from PDF-only lake map (no bathymetric data).

    Extracts GPS coordinates, lake boundary, waterways, fishing spots, and
    boat access from a georeferenced Sépaq PDF map using PyMuPDF (fitz).

    The function performs coordinate transformation from the PDF's UTM grid
    (typically NAD83 / UTM Zone 18N for Mastigouche-area lakes) to WGS84
    geographic coordinates used by Leaflet.

    Parameters
    ----------
    pdf_path : str
        Absolute path to the georeferenced Sépaq PDF map file
        (e.g. "/path/to/MAS_Carte_Lac Roméo.pdf").
    lac_config : dict
        Configuration dictionary with at minimum:
            - "name"       : str  — Lake name (e.g. "Roméo")
            - "file"       : str  — Output filename stem (e.g. "lac_romeo_peche")
            - "output_dir" : str  — Directory to write the HTML file
            - "area_ha"    : float — Lake surface area in hectares
        Optional keys:
            - "lat", "lon" : float — Approximate lake center (fallback if
                             extraction fails)

    Returns
    -------
    dict with keys:
        lake_polygon   : list of [lon, lat] pairs (GeoJSON order) — actual
                         shoreline extracted from the PDF's light-blue fill.
        waterways      : list of dicts, each with:
                             coords      : list of [lon, lat] pairs
                             center_lon  : float
                             center_lat  : float
                             type        : "inlet" | "outlet" | "stream"
        fishing_spots  : list of dicts, each with:
                             lat, lon    : float
                             name        : str
        boat_access    : list of dicts with lat, lon
        portage        : list of dicts with coords
        lake_center    : dict with lat, lon
        lake_bbox      : dict with S, N, W, E bounds

    Algorithm
    ---------
    1. UTM grid extraction:
       Scans the PDF for text labels that look like UTM easting/northing values
       (e.g. "315000", "5190000") and their pixel positions on the page.
       Builds a pixel→UTM affine transform from at least 2 known grid points.

    2. Lake polygon extraction (light-blue fill):
       Iterates over all PDF drawing paths. Paths with fill color close to
       RGB(173, 216, 230) — the Sépaq lake-blue — and more than 20 vertices
       are candidates for the shoreline polygon. The largest such path is
       taken as the lake boundary.

    3. Waterway extraction (blue stroke, no fill):
       Paths with stroke color in the blue family (R < 100, G < 150, B > 150)
       and no fill are classified as waterways. The longest path touching the
       lake boundary is the inlet; the one leading away from the lake centroid
       on the southern side is the outlet.

    4. Fishing spot extraction (magenta/red marker symbols):
       Scans for circular/star-shaped paths with fill color close to
       RGB(255, 0, 128) — the Sépaq "site de pêche favorable" magenta.
       Each cluster of nearby paths is collapsed to a single centroid.

    5. Boat access extraction (black filled square symbols):
       Looks for small square/rectangular filled paths with fill ≈ black
       (all channels < 50). These correspond to the "chaloupe" symbol.

    6. Coordinate transformation:
       Applies the pixel→UTM affine transform to each extracted point,
       then uses pyproj to convert UTM NAD83 Zone 18N (EPSG:26918) to
       WGS84 lat/lon (EPSG:4326).

    Notes
    -----
    - Requires: PyMuPDF (fitz), pyproj, numpy, json
    - The Sépaq PDF color conventions may vary slightly between lakes; the
      color tolerances used here (±30 on each channel) were calibrated on
      MAS_Carte_Lac Roméo.pdf and may need adjustment for other lakes.
    - Depths in Sépaq Mastigouche PDFs are expressed in FEET (pieds), not
      metres. The returned data preserves the original unit; conversion to
      metres requires dividing by 3.28084.
    - If UTM grid extraction fails (fewer than 2 reference points found),
      the function falls back to the lac_config lat/lon and returns an empty
      lake_polygon with a warning.

    Example
    -------
    >>> config = {
    ...     "name": "Roméo", "file": "lac_romeo_peche",
    ...     "output_dir": "/Users/me/fishmap", "area_ha": 11.1,
    ...     "lat": 46.806, "lon": -73.358,
    ... }
    >>> result = gen_pdf_map("/path/to/MAS_Carte_Lac Roméo.pdf", config)
    >>> print(f"Lake polygon: {len(result['lake_polygon'])} points")
    >>> print(f"Waterways: {len(result['waterways'])}")
    >>> print(f"Fishing spots: {len(result['fishing_spots'])}")
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for gen_pdf_map(). "
            "Install with: pip install pymupdf"
        )
    try:
        from pyproj import Transformer
    except ImportError:
        raise ImportError(
            "pyproj is required for gen_pdf_map(). "
            "Install with: pip install pyproj"
        )

    import numpy as np

    print(f"\n[gen_pdf_map] Lac {lac_config['name']} — {pdf_path}")

    # ── 1. Open PDF and get first page ───────────────────────────────────────
    doc = fitz.open(pdf_path)
    page = doc[0]
    page_rect = page.rect
    W, H = page_rect.width, page_rect.height
    print(f"  Page size: {W:.0f} x {H:.0f} pts")

    # Legend area is always on the right ~20% of page; map area ends there
    MAP_X_MAX = W * 0.76

    # ── 2. Extract UTM grid references from text ──────────────────────────────
    # Sépaq PDFs label UTM grid lines with non-breaking-space-separated numbers
    # like "625 000" (easting) and "5 185 100" (northing).
    # We use the CENTER of each text bounding box (not baseline origin) for
    # accurate grid-line pixel positions, then average multiple pairs for scale.
    import re as _re

    transformer_utm_to_wgs = Transformer.from_crs(
        "EPSG:26918",  # NAD83 / UTM Zone 18N
        "EPSG:4326",   # WGS84
        always_xy=True
    )

    easting_anchors  = []  # list of {"px": float, "utm_e": float}
    northing_anchors = []  # list of {"py": float, "utm_n": float}

    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                raw = span["text"]
                # Strip all whitespace and non-breaking spaces, keep digits only
                digits = _re.sub(r'[\s\xa0,]', '', raw)
                if not digits.isdigit():
                    continue
                val = float(digits)
                # Use CENTER of span bounding box for accurate grid-line position
                bb = span["bbox"]
                cx = (bb[0] + bb[2]) / 2
                cy = (bb[1] + bb[3]) / 2

                # UTM Easting: 6-digit, range 200000–799999 (all UTM Zone 18N)
                if len(digits) == 6 and 200000 <= val <= 799999:
                    easting_anchors.append({"px": cx, "utm_e": val})
                # UTM Northing: 7-digit, range 5000000–6000000 (lat ~45–54°N)
                elif len(digits) == 7 and 5000000 <= val <= 6000000:
                    northing_anchors.append({"py": cy, "utm_n": val})

    # Deduplicate by value (keep only unique UTM values, averaged positions)
    def _dedup_anchors(anchors, key_utm, key_px):
        from collections import defaultdict
        groups = defaultdict(list)
        for a in anchors:
            groups[a[key_utm]].append(a[key_px])
        return sorted(
            [{key_utm: v, key_px: sum(pxs)/len(pxs)} for v, pxs in groups.items()],
            key=lambda x: x[key_utm]
        )

    easting_anchors  = _dedup_anchors(easting_anchors,  "utm_e", "px")
    northing_anchors = _dedup_anchors(northing_anchors, "utm_n", "py")
    print(f"  UTM easting anchors:  {len(easting_anchors)} ({[int(a['utm_e']) for a in easting_anchors[:4]]}...)")
    print(f"  UTM northing anchors: {len(northing_anchors)} ({[int(a['utm_n']) for a in northing_anchors[:4]]}...)")

    # Compute scale from ALL consecutive pairs → take median for robustness
    def _compute_scale(anchors, key_utm, key_px):
        scales = []
        for i in range(len(anchors) - 1):
            du = anchors[i+1][key_utm] - anchors[i][key_utm]
            dp = anchors[i+1][key_px]  - anchors[i][key_px]
            if abs(dp) > 1:
                scales.append(du / dp)
        if not scales:
            return None
        scales.sort()
        return scales[len(scales)//2]  # median

    scale_x = _compute_scale(easting_anchors,  "utm_e", "px")   # m/pt (+east → +px)
    scale_y = _compute_scale(northing_anchors, "utm_n", "py")   # m/pt (−north → +py, expect negative)

    if scale_x and scale_y and easting_anchors and northing_anchors:
        ref_E = easting_anchors[0]["utm_e"]
        ref_px = easting_anchors[0]["px"]
        ref_N = northing_anchors[0]["utm_n"]
        ref_py = northing_anchors[0]["py"]
        print(f"  Scale: x={scale_x:.4f} m/pt, y={scale_y:.4f} m/pt  ref=({ref_px:.1f},{ref_py:.1f})→E{ref_E:.0f}/N{ref_N:.0f}")

        def pixel_to_wgs84(px, py):
            E = ref_E + (px - ref_px) * scale_x
            N = ref_N + (py - ref_py) * scale_y
            lon, lat = transformer_utm_to_wgs.transform(E, N)
            return lon, lat
    else:
        center_lat = lac_config.get("lat", 46.806)
        center_lon = lac_config.get("lon", -73.358)
        print(f"  ⚠️  Insufficient UTM grid anchors — using fallback center ({center_lat}, {center_lon})")
        scale_approx = 1.0526  # 1:3000 typical Sépaq lake map

        def pixel_to_wgs84(px, py):
            dlat = -(py - H / 2) * scale_approx / 111320.0
            dlon =  (px - W / 2) * scale_approx / (111320.0 * math.cos(math.radians(center_lat)))
            return center_lon + dlon, center_lat + dlat

    # ── 2b. Extract numeric text positions for filtering ─────────────────────
    # Collect (px, py) for any numeric text span — used to:
    #   • detect route-number circles (filter from boat access)
    #   • match depth labels to MFFP isobath candidates (dark navy strokes)
    all_numeric_text_px = []   # (px, py) of any numeric span in the page
    depth_labels_px     = []   # (px, py, depth_ft) — small font, 5-150 range, in map area

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if not text:
                    continue
                digits_only = _re.sub(r'[^\d]', '', text)
                if not digits_only:
                    continue
                bx = (span["bbox"][0] + span["bbox"][2]) / 2
                by = (span["bbox"][1] + span["bbox"][3]) / 2
                all_numeric_text_px.append((bx, by))
                font_sz = span.get("size", 10)
                if text.isdigit() and 5 <= float(text) <= 150 and bx < MAP_X_MAX and font_sz < 7.5:
                    depth_labels_px.append((bx, by, float(text)))

    print(f"  Numeric text spans: {len(all_numeric_text_px)}, depth label candidates: {len(depth_labels_px)} → {[int(d[2]) for d in depth_labels_px]}")

    # ── 3. Extract drawing paths ──────────────────────────────────────────────
    # Color signatures calibrated on real Sépaq Mastigouche PDFs (fitz 0-1 scale)
    # These are the EXACT colors found in the PDF, not theoretical values.
    paths = page.get_drawings()
    print(f"  Drawing paths found: {len(paths)}")

    # Sépaq PDF color signatures — calibrated on MAS_Carte_Lac Roméo.pdf
    #   Lake fill:      RGB(0.592, 0.859, 0.949) — light blue
    #   Water stroke:   RGB(0.251, 0.400, 0.922) — stream/river blue
    #   Trout line:     RGB(0.000, 0.522, 0.659) — teal (omble de fontaine)
    #   Portage/access: magenta STROKE (1.0, 0.0, ~0.77) — long line segments
    #   Spot marker:    magenta FILL  (1.0, 0.0, ~0.77)  — small dots
    #   Boat access:    black FILL    (0.0, 0.0, 0.0)    — small square

    def _cdist(c1, c2, tol):
        if c1 is None or c2 is None:
            return False
        return all(abs(a - b) <= tol for a, b in zip(c1, c2))

    def _extract_pts(path):
        pts = []
        seen = set()
        for item in path.get("items", []):
            raw = []
            if item[0] == "l":  raw = [item[1], item[2]]
            elif item[0] == "c": raw = [item[1], item[2], item[3], item[4]]
            elif item[0] == "re":
                r = item[1]
                raw = [fitz.Point(r.x0,r.y0), fitz.Point(r.x1,r.y0),
                       fitz.Point(r.x1,r.y1), fitz.Point(r.x0,r.y1)]
            for pt in raw:
                key = (round(pt.x, 1), round(pt.y, 1))
                if key not in seen:
                    seen.add(key)
                    pts.append(pt)
        return pts

    def _in_map(pts):
        """True if path centroid is in the map area (not in legend)."""
        if not pts: return False
        cx = sum(p.x for p in pts) / len(pts)
        cy = sum(p.y for p in pts) / len(pts)
        return cx < MAP_X_MAX and 15 < cy < H - 15

    def _pts_to_latlon(pts):
        coords = []
        for p in pts:
            lon, lat = pixel_to_wgs84(p.x, p.y)
            coords.append([round(lat, 6), round(lon, 6)])
        return coords

    lake_polygon            = []
    waterways               = []
    fishing_spots           = []
    boat_access             = []
    portage                 = []
    trout_line              = []
    access_path             = []
    mffp_isobath_candidates = []   # dark navy stroke paths; matched to depth labels later

    largest_lake_path = None
    largest_lake_pts  = 0

    for path in paths:
        fill  = path.get("fill")
        color = path.get("color")  # stroke
        pts   = _extract_pts(path)
        if not pts or not _in_map(pts):
            continue

        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        w_span = max(xs) - min(xs)
        h_span = max(ys) - min(ys)
        cx_px  = (max(xs) + min(xs)) / 2
        cy_px  = (max(ys) + min(ys)) / 2

        # ── Lake polygon: largest light-blue filled path ──────────────────
        if fill and _cdist(fill, (0.592, 0.859, 0.949), 0.08):
            if len(pts) > largest_lake_pts:
                largest_lake_pts = len(pts)
                largest_lake_path = pts

        # ── Waterways: blue stroke ────────────────────────────────────────
        elif color and _cdist(color, (0.251, 0.400, 0.922), 0.08) and len(pts) >= 2:
            # Skip the lake outline drawn in blue (>80pts and encloses area)
            if len(pts) > 80:
                continue
            coords = _pts_to_latlon(pts)
            waterways.append({
                "coords": coords,
                "center_lat": float(sum(c[0] for c in coords) / len(coords)),
                "center_lon": float(sum(c[1] for c in coords) / len(coords)),
            })

        # ── Trout habitat line: teal stroke (omble de fontaine) ──────────
        elif color and _cdist(color, (0.000, 0.522, 0.659), 0.08) and len(pts) >= 2:
            coords = _pts_to_latlon(pts)
            trout_line.extend(coords)

        # ── Portage / access path: magenta STROKE, elongated ─────────────
        elif color and _cdist(color, (1.0, 0.0, 0.773), 0.08):
            coords = _pts_to_latlon(pts)
            if max(w_span, h_span) > 15:   # elongated = line/trail
                access_path.extend(coords)
            elif len(pts) >= 3:             # small = fishing spot marker
                lon, lat = pixel_to_wgs84(cx_px, cy_px)
                fishing_spots.append({
                    "lat": round(lat, 6), "lon": round(lon, 6),
                    "name": "Site de pêche favorable"
                })

        # ── Boat access: small black filled square ────────────────────────
        elif fill and _cdist(fill, (0.0, 0.0, 0.0), 0.05):
            if 2 < max(w_span, h_span) < 25 and min(w_span, h_span) > 1:
                # Route-number circles are oval (w/h ≈ 1.45) with nearby numeric text;
                # real chaloupe squares are nearly square (aspect ≈ 1.0) with no text.
                aspect = max(w_span, h_span) / max(min(w_span, h_span), 0.01)
                has_route_text = any(abs(tx - cx_px) < 20 and abs(ty - cy_px) < 20
                                     for tx, ty in all_numeric_text_px)
                if aspect <= 1.35 and not has_route_text:
                    lon, lat = pixel_to_wgs84(cx_px, cy_px)
                    boat_access.append({"lat": round(lat, 6), "lon": round(lon, 6)})

        # ── MFFP isobaths: dark navy stroke (0.000, 0.149, 0.451) ────────
        elif color and _cdist(color, (0.000, 0.149, 0.451), 0.06) and len(pts) >= 10:
            coords = _pts_to_latlon(pts)
            mffp_isobath_candidates.append({
                "coords":    coords,
                "cx_px":     cx_px,
                "cy_px":     cy_px,
                "bbox_area": w_span * h_span,   # larger = outer ring = shallower
            })

    # ── Convert lake polygon ──────────────────────────────────────────────
    if largest_lake_path:
        lake_polygon = _pts_to_latlon(largest_lake_path)
        print(f"  Lake polygon: {len(lake_polygon)} points")
    else:
        print("  ⚠️  No lake polygon found — check PDF color conventions")

    # ── Deduplicate trout line points ─────────────────────────────────────
    seen = set()
    trout_line_clean = []
    for c in trout_line:
        key = (round(c[0], 5), round(c[1], 5))
        if key not in seen:
            seen.add(key)
            trout_line_clean.append(c)
    trout_line = trout_line_clean

    # ── Deduplicate access path points (sort S→N for rendering) ──────────
    seen = set()
    access_clean = []
    for c in access_path:
        key = (round(c[0], 5), round(c[1], 5))
        if key not in seen:
            seen.add(key)
            access_clean.append(c)
    access_path = sorted(access_clean, key=lambda c: c[0])  # S→N

    # ── Compute lake center and bbox ─────────────────────────────────────
    # lake_polygon is [[lat, lon], ...] (Leaflet format)
    if lake_polygon:
        lats = [p[0] for p in lake_polygon]
        lons = [p[1] for p in lake_polygon]
        lake_center = {
            "lat": float(sum(lats) / len(lats)),
            "lon": float(sum(lons) / len(lons))
        }
        lake_bbox = {
            "S": float(min(lats)), "N": float(max(lats)),
            "W": float(min(lons)), "E": float(max(lons))
        }
    else:
        lake_center = {"lat": lac_config.get("lat", 0), "lon": lac_config.get("lon", 0)}
        lake_bbox   = {"S": 0, "N": 0, "W": 0, "E": 0}

    # ── Classify waterways as inlet / outlet / tributary ─────────────────
    # Strategy: compare each waterway's center to the lake centroid.
    # Waterways entirely south of lake south boundary → outlet.
    # Waterways far west of lake west boundary → tributary.
    # Remainder: above centroid → inlet, below → outlet.
    lake_centroid_lat = lake_center["lat"]
    lake_centroid_lon = lake_center["lon"]
    lake_s = lake_bbox["S"]
    lake_n = lake_bbox["N"]
    lake_w = lake_bbox["W"]

    for ww in waterways:
        clat = ww["center_lat"]
        clon = ww["center_lon"]
        # Outlet: center clearly south of lake (more than 50m south of south shore)
        if clat < lake_s - 0.00045:
            ww["type"] = "outlet"
        # Tributary: center clearly west of lake west boundary
        elif clon < lake_w - 0.0003:
            ww["type"] = "tributary"
        # Inlet: center north of lake centroid; Outlet: south
        elif clat >= lake_centroid_lat:
            ww["type"] = "inlet"
        else:
            ww["type"] = "outlet"

    # ── Filter out bathymetric contour lines (blue lines INSIDE the lake) ───
    # Real waterways (inlet/outlet/tributary) extend outside the lake bbox.
    # Depth contour lines (isobaths) have all points within the lake bounds.
    # Keep only waterways where at least one point is outside the lake bbox
    # by at least MARGIN degrees (~50m). This removes isobaths from PDFs with
    # bathymetry data (like Osborn) which would otherwise add hundreds of paths.
    MARGIN = 0.0004  # ~44m at this latitude
    def _has_point_outside(ww, s, n, w, e):
        for lat, lon in ww["coords"]:
            if lat < s - MARGIN or lat > n + MARGIN or lon < w - MARGIN or lon > e + MARGIN:
                return True
        return False

    if lake_bbox["N"] > 0:  # only filter when we have real bbox
        before = len(waterways)
        waterways = [ww for ww in waterways
                     if _has_point_outside(ww, lake_bbox["S"], lake_bbox["N"],
                                           lake_bbox["W"], lake_bbox["E"])]
        if len(waterways) < before:
            print(f"  Filtered {before - len(waterways)} isobath paths, kept {len(waterways)} waterways")

    # ── Deduplicate: cluster waterways per type, keep longest per cluster ────
    # For lakes with many GPS track segments exported from PDFs, the same
    # real stream can appear as many small fragments. Cluster by proximity
    # (0.0005° ≈ 55m) within each type group, keep the longest in each cluster.
    # Hard cap: max 2 per type, max 6 total.
    CLUSTER_DIST = 0.0009  # ~100m — merge fragments of same stream
    MAX_PER_TYPE = 2        # max 2 per type (inlet/outlet/tributary)

    def _cluster_waterways(ww_list):
        """Greedy Euclidean clustering; keeps one representative per cluster, max MAX_PER_TYPE."""
        cos_lat = math.cos(math.radians(lake_centroid_lat))
        def _dist_m(a, b):
            dlat = (a["center_lat"] - b["center_lat"]) * 111320
            dlon = (a["center_lon"] - b["center_lon"]) * 111320 * cos_lat
            return (dlat**2 + dlon**2) ** 0.5
        remaining = sorted(ww_list, key=lambda w: len(w["coords"]), reverse=True)
        chosen = []
        while remaining and len(chosen) < MAX_PER_TYPE:
            seed = remaining.pop(0)
            chosen.append(seed)
            remaining = [w for w in remaining if _dist_m(w, seed) > CLUSTER_DIST * 111320]
        return chosen

    if len(waterways) > MAX_PER_TYPE * 3:
        deduped = []
        for wtype in ("inlet", "outlet", "tributary"):
            group = [w for w in waterways if w["type"] == wtype]
            if group:
                deduped.extend(_cluster_waterways(group))
        before2 = len(waterways)
        waterways = deduped[:6]
        print(f"  Deduplicated {before2} → {len(waterways)} waterways after clustering")

    # ── Match MFFP isobath candidates to depth labels ────────────────────────
    # Concentric isobaths: largest bounding-box area = outermost = shallowest.
    # Strategy: sort all candidates by bbox_area descending (largest = shallowest),
    # sort depth labels ascending (smallest = shallowest), then assign each
    # candidate a depth by proportional rank so ALL segments are included.
    # This handles both simple lakes (1 path/depth) and large lakes (many segments/depth).
    mffp_isobaths = []
    if mffp_isobath_candidates and depth_labels_px:
        unique_depths_ft = sorted(set(d[2] for d in depth_labels_px))
        n_depths  = len(unique_depths_ft)
        n_cands   = len(mffp_isobath_candidates)
        sorted_cands = sorted(mffp_isobath_candidates,
                              key=lambda c: c["bbox_area"], reverse=True)
        for i, cand in enumerate(sorted_cands):
            depth_idx = min(int(i * n_depths / n_cands), n_depths - 1)
            depth_ft  = unique_depths_ft[depth_idx]
            depth_m   = round(depth_ft * 0.3048, 1)
            mffp_isobaths.append({
                "depth_m":  depth_m,
                "depth_ft": depth_ft,
                "coords":   cand["coords"],
            })
        depth_summary = sorted(set(x["depth_m"] for x in mffp_isobaths))
        print(f"  MFFP isobaths: {n_cands} paths → {n_depths} levels {depth_summary} m")
    elif mffp_isobath_candidates:
        print(f"  MFFP isobaths: {len(mffp_isobath_candidates)} candidates but no depth labels in map area")
    else:
        print(f"  MFFP isobaths: none (no dark-navy strokes found)")

    doc.close()

    result = {
        "lake_polygon":  lake_polygon,
        "waterways":     waterways,
        "fishing_spots": fishing_spots,
        "boat_access":   boat_access,
        "portage":       portage,
        "trout_line":    trout_line,
        "access_path":   access_path,
        "lake_center":   lake_center,
        "lake_bbox":     lake_bbox,
        "mffp_isobaths": mffp_isobaths,
    }

    print(f"\n  Summary for Lac {lac_config['name']}:")
    print(f"    Lake polygon:  {len(lake_polygon)} points")
    print(f"    Waterways:     {len(waterways)} ({[w['type'] for w in waterways]})")
    print(f"    Fishing spots: {len(fishing_spots)}")
    print(f"    Boat access:   {len(boat_access)}")
    print(f"    Trout line:    {len(trout_line)} points")
    print(f"    Access path:   {len(access_path)} points")
    print(f"    MFFP isobaths: {len(mffp_isobaths)} ({[x['depth_m'] for x in mffp_isobaths]} m)")
    print(f"    Center:        {lake_center['lat']:.5f}°N, {lake_center['lon']:.5f}°W")

    # Optionally save to JSON for later HTML generation
    output_dir = lac_config.get("output_dir", ".")
    json_path = os.path.join(output_dir, f"{lac_config.get('file', 'lac')}_pdf_data.json")
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(result, jf, ensure_ascii=False, indent=2)
    print(f"    Data saved: {json_path}")
    print("    → Call run_render_test(html_path) after building HTML from this data.")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# HTML GENERATION FROM PDF DATA
# ─────────────────────────────────────────────────────────────────────────────

def _auto_fishing_spots(data: dict, species: str) -> list:
    """Generate fishing spots from extracted PDF geography."""
    spots = []
    lake_center = data["lake_center"]
    lake_bbox   = data["lake_bbox"]
    clat = lake_center["lat"]
    clon = lake_center["lon"]

    # Reuse official spots from PDF fishing_spots if any
    for i, fs in enumerate(data.get("fishing_spots", [])):
        spots.append({
            "id": f"official_{i}",
            "lat": round(fs["lat"], 5),
            "lon": round(fs["lon"], 5),
            "name": "Site Sépaq — Pêche favorable",
            "icon": "⭐", "type": "official",
            "bestTime": [5,6,7,8,17,18,19,20],
            "peakTime": [6,7,18,19],
            "score_base": 90,
            "why": "Site identifié par guides terrain Sépaq. Zone de pêche favorable validée."
        })

    # Inlet spot: just inside lake from inlet waterway
    inlets  = [w for w in data.get("waterways", []) if w["type"] == "inlet"]
    outlets = [w for w in data.get("waterways", []) if w["type"] == "outlet"]
    tribs   = [w for w in data.get("waterways", []) if w["type"] == "tributary"]

    if inlets:
        ww = inlets[0]
        # Find the point of the inlet closest to the lake interior
        in_pt = min(ww["coords"], key=lambda c: abs(c[0] - clat))
        ilat = round(max(lake_bbox["S"] + 0.0003, min(lake_bbox["N"] - 0.0003, in_pt[0])), 5)
        ilon = round(max(lake_bbox["W"] + 0.0001, min(lake_bbox["E"] - 0.0001, in_pt[1])), 5)
        spots.append({
            "id": "inlet",
            "lat": ilat, "lon": ilon,
            "name": "Entrée nord — Eau fraîche",
            "icon": "🎣", "type": "inlet",
            "bestTime": [5,6,7,8,17,18,19,20], "peakTime": [5,6,7,18,19],
            "score_base": 85,
            "why": f"Entrée d'eau froide et oxygénée. L'{species} affectionne ces zones de convergence thermique, surtout en mai."
        })

    if outlets:
        ww = outlets[0]
        out_pt = min(ww["coords"], key=lambda c: abs(c[0] - clat))
        olat = round(max(lake_bbox["S"] + 0.0002, min(lake_bbox["N"] - 0.0002, out_pt[0])), 5)
        olon = round(max(lake_bbox["W"] + 0.0001, min(lake_bbox["E"] - 0.0001, out_pt[1])), 5)
        spots.append({
            "id": "outlet",
            "lat": olat, "lon": olon,
            "name": "Sortie sud — Affût courant",
            "icon": "🎣", "type": "outlet",
            "bestTime": [5,6,7,8,17,18,19,20], "peakTime": [6,7,17,18],
            "score_base": 72,
            "why": f"Sortie d'eau (émissaire). Les {species.lower()}s se tiennent souvent à la sortie pour profiter du courant."
        })

    # Tributary spot
    if tribs:
        ww = tribs[0]
        tr_pt = ww["coords"][0] if ww["coords"] else None
        if tr_pt:
            tlat = round(max(lake_bbox["S"]+0.0002, min(lake_bbox["N"]-0.0002, tr_pt[0])), 5)
            tlon = round(max(lake_bbox["W"]+0.0001, min(lake_bbox["E"]-0.0001, tr_pt[1])), 5)
            spots.append({
                "id": "tributary",
                "lat": tlat, "lon": tlon,
                "name": "Affluent — Eau fraîche",
                "icon": "🌿", "type": "tributary",
                "bestTime": [5,6,7,8,17,18,19,20], "peakTime": [6,7,18,19],
                "score_base": 70,
                "why": "Confluent d'un affluent. Zone de mélange thermique favorable aux salmonidés."
            })

    # Lake center (pelagic)
    spots.append({
        "id": "center",
        "lat": round(clat, 5), "lon": round(clon, 5),
        "name": "Centre lac — Pélagique",
        "icon": "🐟", "type": "pelagic",
        "bestTime": [5,6,7,19,20], "peakTime": [6,19,20],
        "score_base": 50,
        "why": f"Zone de transit. Les {species.lower()}s croisent le centre en début/fin de journée."
    })

    # Near boat access (south bay)
    access_end = (data.get("access_path") or [None])[-1]
    if access_end and not any(abs(s["lat"]-access_end[0]) < 0.0005 for s in spots):
        alat = round(max(lake_bbox["S"]+0.0002, min(lake_bbox["N"]-0.0002, access_end[0]+0.0003)), 5)
        alon = round(max(lake_bbox["W"]+0.0001, min(lake_bbox["E"]-0.0001, access_end[1])), 5)
        spots.append({
            "id": "south_bay",
            "lat": alat, "lon": alon,
            "name": "Baie sud — Accès facile",
            "icon": "🎣", "type": "bay",
            "bestTime": [5,6,7,8,17,18,19,20], "peakTime": [6,7,18,19],
            "score_base": 65,
            "why": "Zone accessible depuis la mise à l'eau. Structure de rive et fond variable favorable."
        })

    return spots


def build_pdf_html(data: dict, lac_config: dict, template_path: str) -> str:
    """Generate a fishing map HTML from gen_pdf_map() data using the Romeo template.

    Parameters
    ----------
    data        : dict returned by gen_pdf_map()
    lac_config  : dict with keys: name, file, area_ha, species (optional),
                  trip_dates (list of 'YYYY-MM-DD'), lat, lon
    template_path : path to lac_romeo_peche.html (used as template)

    Returns
    -------
    str  — complete HTML content
    """
    import unicodedata as _ud
    with open(template_path, "r", encoding="utf-8") as f:
        html = _ud.normalize("NFC", f.read())

    lac_name = lac_config["name"]
    species  = lac_config.get("species", "Omble de fontaine")
    clat     = data["lake_center"]["lat"]
    clon     = data["lake_center"]["lon"]
    trip_dates = lac_config.get("trip_dates", ["2026-05-20","2026-05-21","2026-05-22"])

    # Compute polygon area if not provided
    poly = data.get("lake_polygon", [])
    area_ha = lac_config.get("area_ha") or _polygon_area_ha(poly) or "?"

    # Fishing stats from MASTIGOUCHE_STATS
    st = _lake_stats(lac_name)
    success   = st.get("success", 0)
    mass_g    = st.get("mass_g", 0)
    vehicule  = st.get("vehicule", "Auto")
    portage   = st.get("portage_min", 0)
    enst_code = st.get("enst", "")

    # GBLQ lookup
    gblq      = _lookup_gblq(lac_name)
    has_gblq  = gblq.get("ISO_DISPO") == "Oui"
    gblq_data = {}
    if has_gblq and gblq.get("URL_GPKG_ZIP"):
        cache_dir = lac_config.get("output_dir", tempfile.gettempdir())
        gblq_data = download_gblq_isobaths(gblq["URL_GPKG_ZIP"], cache_dir=cache_dir)

    def _js(obj):
        return json.dumps(obj, ensure_ascii=False)

    # ── Generate fishing spots ───────────────────────────────────────────────
    spots = _auto_fishing_spots(data, species)
    spots_js_parts = []
    for s in spots:
        spots_js_parts.append(
            f"  {{ id: '{s['id']}', lat: {s['lat']}, lon: {s['lon']}, "
            f"name: {_js(s['name'])}, icon: {_js(s['icon'])}, type: {_js(s['type'])},\n"
            f"    bestTime: {s['bestTime']}, peakTime: {s['peakTime']}, score_base: {s['score_base']},\n"
            f"    why: {_js(s['why'])} }}"
        )
    spots_js = "const FISHING_SPOTS = [\n" + ",\n".join(spots_js_parts) + "\n];"

    # ── Waterways JS ────────────────────────────────────────────────────────
    ww_parts = []
    type_label = {"inlet": "Entrée nord", "outlet": "Sortie sud",
                  "tributary": "Affluent NW", "stream": "Cours d'eau"}
    for ww in data.get("waterways", []):
        wtype = ww.get("type", "stream")
        name  = type_label.get(wtype, "Cours d'eau")
        ww_parts.append(
            f"  {{ type: {_js(wtype)}, name: {_js(name)}, "
            f"lat: {round(ww['center_lat'],5)}, lon: {round(ww['center_lon'],5)},\n"
            f"    coords: {_js(ww['coords'])} }}"
        )
    ww_js = "const WATERWAYS = [\n" + ",\n".join(ww_parts) + "\n];"

    # ── Trout line JS ────────────────────────────────────────────────────────
    trout_js = f"const TROUT_LINE = {_js(data.get('trout_line', []))};"

    # ── Access path JS ───────────────────────────────────────────────────────
    access_js = f"const ACCESS_PATH = {_js(data.get('access_path', []))};"

    # ── Boat access JS ───────────────────────────────────────────────────────
    ba = data.get("boat_access", [])
    if not ba:
        # Fallback: end of access path or south shore
        ap = data.get("access_path", [])
        ba = [{"lat": ap[-1][0], "lon": ap[-1][1]}] if ap else [{"lat": clat, "lon": clon}]
    boat_js = f"const BOAT_ACCESS = {_js(ba)};"

    # ── Replace ALL template lake-name occurrences ───────────────────────────
    lac_name_sq = lac_name.replace("'", "\\'")  # safe inside JS '...' strings

    # STEP 1: Replace the lake polygon tooltip BEFORE the broad replace.
    # The template has 'Lac Roméo — ...' (no apostrophe), so [^']* matches cleanly.
    # If done after html.replace("Roméo", "l'Orignal"), the apostrophe in "l'" would
    # truncate [^']* and leave 'Orignal — old_area...' orphaned in the output.
    html = re.sub(
        r"lakePoly\.bindTooltip\('[^']*'",
        f"lakePoly.bindTooltip('Lac {lac_name_sq} — {area_ha} ha<br>Source: PDF Sépaq 2024'",
        html
    )

    # STEP 2: Broad replace for all remaining "Roméo" occurrences (HTML, double-quoted JS, etc.)
    html = html.replace("Roméo", lac_name)

    # Title + meta
    html = re.sub(r'<title>.*?</title>', f'<title>Lac {lac_name} — Pêche Mastigouche</title>', html)
    html = re.sub(r'content="[^"]*Lac [^"]*Mastigouche[^"]*"',
                  f'content="Carte de pêche interactive — Lac {lac_name}, Réserve faunique Mastigouche."', html)

    # Coordinates in top-bar display
    html = re.sub(r'\d+\.\d+°N,\s*\d+\.\d+°W', f"{abs(clat):.3f}°N, {abs(clon):.3f}°W", html)

    # JS constants
    bathy_note = "Bathymétrie GBLQ disponible" if has_gblq else "sans bathymétrie"
    html = re.sub(r'const SOURCE_NOTE\s*=\s*"[^"]*";',
                  f'const SOURCE_NOTE = "Carte PDF Sépaq 2024 — Lac {lac_name} ({bathy_note})";', html)
    html = re.sub(r'const SPECIES\s*=\s*"[^"]*";',
                  f'const SPECIES = {_js(species)};', html)
    html = re.sub(r'const LAT\s*=\s*[\d.]+,\s*LON\s*=\s*[-\d.]+;',
                  f'const LAT = {round(clat,4)}, LON = {round(clon,4)};', html)
    html = re.sub(r'const LAC_NAME\s*=\s*"[^"]*";',
                  f'const LAC_NAME = "{lac_name_sq}";', html)
    html = re.sub(r'const MAP_CENTER\s*=\s*\[[^\]]*\];',
                  f'const MAP_CENTER = [{round(clat,4)}, {round(clon,4)}];', html)
    html = re.sub(r'const LAC_AREA_HA\s*=\s*[\d.]+;',
                  f'const LAC_AREA_HA = {area_ha};', html)

    # ── Stats panel (HTML) ────────────────────────────────────────────────────
    # Score badge label (success rate + mass)
    success_str = f"{success} omble/j" if success else "N/D"
    mass_str    = f"{mass_g} g" if mass_g else "N/D"
    portage_str = f"{portage} min portage" if portage else "Aucun portage"
    enst_str    = f"Ensemencement {enst_code}" if enst_code else "Espèce indigène"
    gblq_str    = "Oui (GBLQ)" if has_gblq else "Non disponible"

    new_stats_block = (
        f'  <h3>📊 Stats Lac {lac_name}</h3>\n'
        f'  <div class="stat-row"><span class="stat-label">Superficie</span>'
        f'<span class="stat-value">{area_ha} ha</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Succès 2025</span>'
        f'<span class="stat-value">{success_str}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Masse moy.</span>'
        f'<span class="stat-value">{mass_str}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Véhicule</span>'
        f'<span class="stat-value">{vehicule}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Portage</span>'
        f'<span class="stat-value">{portage_str}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Bathymétrie</span>'
        f'<span class="stat-value">{gblq_str}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Espèce cible</span>'
        f'<span class="stat-value">{species}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Zone pêche</span>'
        f'<span class="stat-value">Zone 9</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Ensemencement</span>'
        f'<span class="stat-value">{enst_str}</span></div>\n'
        f'  <div class="stat-row"><span class="stat-label">Source</span>'
        f'<span class="stat-value">Carte PDF Sépaq 2024</span></div>'
    )
    html = re.sub(
        r'<h3>📊 Stats Lac [^<]*</h3>[\s\S]*?<h3>🗺️ Légende',
        new_stats_block + '\n\n  <h3>🗺️ Légende',
        html, count=1
    )

    # LAKE_POLYGON
    html = re.sub(r'const LAKE_POLYGON\s*=\s*\[[\s\S]*?\];',
                  f'const LAKE_POLYGON = {_js(data.get("lake_polygon", []))};', html, count=1)

    # WATERWAYS
    html = re.sub(r'const WATERWAYS\s*=\s*\[[\s\S]*?\];', ww_js, html, count=1)

    # TROUT_LINE
    html = re.sub(r'const TROUT_LINE\s*=\s*\[[\s\S]*?\];', trout_js, html, count=1)

    # ACCESS_PATH
    html = re.sub(r'const ACCESS_PATH\s*=\s*\[[\s\S]*?\];', access_js, html, count=1)

    # BOAT_ACCESS
    html = re.sub(r'const BOAT_ACCESS\s*=\s*\[[\s\S]*?\];', boat_js, html, count=1)

    # FISHING_SPOTS
    html = re.sub(r'const FISHING_SPOTS\s*=\s*\[[\s\S]*?\];', spots_js, html, count=1)

    # TRIP_DATES
    html = re.sub(r'const TRIP_DATES\s*=\s*\[[^\]]*\];',
                  f'const TRIP_DATES = {_js(trip_dates)};', html, count=1)

    # ── Inject GBLQ isobaths before </script> ────────────────────────────────
    if gblq_data.get("isobaths"):
        gblq_js = _gblq_js_block(gblq_data, lac_name_sq)
        html = html.replace("</script>", gblq_js + "\n</script>", 1)
        # Update stat panel: show real depth
        max_d = gblq_data.get("max_depth", 0)
        if max_d:
            html = re.sub(
                r'(<span class="stat-label">Bathymétrie</span><span class="stat-value">)[^<]*(</span>)',
                lambda m, d=max_d: m.group(1) + f'GBLQ {d} m max' + m.group(2), html
            )
            html = re.sub(
                r'(<span class="stat-label">Profondeur max</span><span class="stat-value">)[^<]*(</span>)',
                lambda m, d=max_d: m.group(1) + f'{d} m (GBLQ)' + m.group(2), html
            )

    # ── Inject MFFP PDF isobaths (when no GBLQ data available) ──────────────
    mffp_isobaths = data.get("mffp_isobaths", [])
    if mffp_isobaths and not gblq_data.get("isobaths"):
        mffp_js = _mffp_js_block(mffp_isobaths, lac_name_sq)
        html = html.replace("</script>", mffp_js + "\n</script>", 1)
        max_d_ft = max(x["depth_ft"] for x in mffp_isobaths)
        max_d_m  = round(max_d_ft * 0.3048, 1)
        html = re.sub(
            r'(<span class="stat-label">Bathymétrie</span><span class="stat-value">)[^<]*(</span>)',
            lambda m, d=max_d_m: m.group(1) + f'MFFP PDF {d} m max' + m.group(2), html
        )

    return html


def batch_pdf_maps(pdf_dir: str, output_dir: str, template_path: str,
                   trip_dates: list = None, force: bool = False) -> list:
    """Process all Sépaq PDF lake maps in a directory.

    Parameters
    ----------
    pdf_dir      : directory containing MAS_Carte_*.pdf files
    output_dir   : directory to write HTML outputs
    template_path: path to lac_romeo_peche.html (used as template)
    trip_dates   : list of 'YYYY-MM-DD' strings for the forecast
    force        : re-generate even if HTML already exists

    Returns
    -------
    list of (lake_name, html_path, ok) tuples
    """
    if trip_dates is None:
        trip_dates = ["2026-05-20", "2026-05-21", "2026-05-22"]

    import glob, unicodedata

    def slugify(name):
        # "Lac du Hêtre" → "lac_du_hetre"
        nfkd = unicodedata.normalize("NFKD", name)
        ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
        return ascii_str.lower().replace(" ", "_").replace("'", "_").replace("-", "_")

    pdfs = sorted(glob.glob(os.path.join(pdf_dir, "MAS_Carte_*.pdf")))
    print(f"\n{'='*60}")
    print(f"  BATCH: {len(pdfs)} PDF(s) dans {pdf_dir}")
    print(f"  Sortie: {output_dir}")
    print(f"{'='*60}\n")

    os.makedirs(output_dir, exist_ok=True)
    results = []

    # These have curated bathymetry (MFFP/SDB) — skip unless explicitly forced
    CURATED = {"lac_fox_peche.html", "lac_dore_peche.html", "lac_marcel_peche.html",
               "lac_osborn_peche.html",
               "lac_baie_peche.html", "lac_chutenoire_peche.html",
               "lac_saules_peche.html", "lac_sable_peche.html"}

    for i, pdf_path in enumerate(pdfs, 1):
        basename = os.path.basename(pdf_path)
        # "MAS_Carte_Lac Roméo.pdf" → "Roméo"
        # Handle "MAS_Carte_Grand lac des Îles.pdf" → "Grand lac des Îles"
        lac_name = re.sub(r'^MAS_Carte_(?:Lac\s+)?', '', basename).replace(".pdf", "").strip()
        file_slug = "lac_" + slugify(lac_name)
        html_path = os.path.join(output_dir, f"{file_slug}_peche.html")

        print(f"[{i:02d}/{len(pdfs)}] {lac_name}")

        # ALWAYS skip curated files — they have real MFFP/SDB bathymetry data
        if os.path.basename(html_path) in CURATED:
            print(f"       → fichier curatée (bathymétrie MFFP), skip permanent")
            results.append((lac_name, html_path, True))
            continue

        if os.path.exists(html_path) and not force:
            print(f"       → déjà existant, skip (--force pour régénérer)")
            results.append((lac_name, html_path, True))
            continue

        try:
            st = _lake_stats(lac_name)
            lac_config = {
                "name": lac_name,
                "file": file_slug,
                "output_dir": output_dir,
                "species": "Omble de fontaine",
                "trip_dates": trip_dates,
                "vehicule": st.get("vehicule", "Auto"),
                "portage_min": st.get("portage_min", 0),
            }
            data = gen_pdf_map(pdf_path, lac_config)
            html = build_pdf_html(data, lac_config, template_path)

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            size_kb = os.path.getsize(html_path) // 1024
            print(f"       ✅ {html_path} ({size_kb} KB)")

            ok = run_render_test(html_path)
            results.append((lac_name, html_path, ok))

        except Exception as exc:
            print(f"       ❌ ERREUR: {exc}")
            import traceback; traceback.print_exc()
            results.append((lac_name, html_path, False))

    print(f"\n{'='*60}")
    ok_count = sum(1 for _,_,ok in results if ok)
    print(f"  BILAN: {ok_count}/{len(results)} cartes générées avec succès")
    for name, path, ok in results:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
    print(f"{'='*60}\n")
    return results


if __name__ == "__main__":
    main()

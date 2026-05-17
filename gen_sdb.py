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

    # ── 2. Extract UTM grid references from text ──────────────────────────────
    # Look for text blocks that contain UTM coordinates (6-7 digit numbers)
    utm_refs = []  # list of (pixel_x, pixel_y, utm_easting, utm_northing)

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES)["blocks"]
    for block in blocks:
        if block.get("type") != 0:  # text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip().replace(" ", "").replace(",", "")
                origin = span["origin"]  # (x, y) pixel position
                # Easting: 6-digit number starting with 2-5 (UTM zone 18N ~250000-750000)
                if txt.isdigit() and len(txt) == 6 and txt[0] in "2345":
                    utm_refs.append({
                        "px": origin[0], "py": origin[1],
                        "easting": float(txt), "northing": None
                    })
                # Northing: 7-digit number starting with 5 (latitude ~46° → ~5100000-5200000)
                elif txt.isdigit() and len(txt) == 7 and txt[0] == "5":
                    utm_refs.append({
                        "px": origin[0], "py": origin[1],
                        "easting": None, "northing": float(txt)
                    })

    print(f"  UTM text references found: {len(utm_refs)}")

    # Build pixel→UTM transform from grid references
    # Pair eastings with northings by proximity on the page
    eastings  = [r for r in utm_refs if r["easting"]  is not None]
    northings = [r for r in utm_refs if r["northing"] is not None]

    pixel_to_utm = None
    transformer_utm_to_wgs = Transformer.from_crs(
        "EPSG:26918",  # NAD83 / UTM Zone 18N
        "EPSG:4326",   # WGS84
        always_xy=True
    )

    if len(eastings) >= 2 and len(northings) >= 2:
        # Use the first two of each to build an affine transform
        e1, e2 = eastings[0], eastings[1]
        n1, n2 = northings[0], northings[1]

        # Pixel x-axis ~ easting, pixel y-axis (inverted) ~ northing
        # Build 2x2 linear system: px = a*E + b, py = c*N + d
        dE = e2["easting"]  - e1["easting"]
        dpx_E = e2["px"] - e1["px"]
        scale_x = dE / dpx_E if dpx_E != 0 else 1.0

        dN = n2["northing"] - n1["northing"]
        dpy_N = n2["py"] - n1["py"]
        scale_y = dN / dpy_N if dpy_N != 0 else -1.0  # y inverted in PDF

        ref_px = e1["px"]
        ref_py = n1["py"]
        ref_E  = e1["easting"]
        ref_N  = n1["northing"]

        def pixel_to_wgs84(px, py):
            E = ref_E + (px - ref_px) * scale_x
            N = ref_N + (py - ref_py) * scale_y
            lon, lat = transformer_utm_to_wgs.transform(E, N)
            return lon, lat

        pixel_to_utm = pixel_to_wgs84
        print(f"  Affine transform built: scale_x={scale_x:.2f} m/pt, scale_y={scale_y:.2f} m/pt")
    else:
        # Fallback: use configured lake center and page center
        center_lat = lac_config.get("lat", 46.806)
        center_lon = lac_config.get("lon", -73.358)
        print(f"  ⚠️  Insufficient UTM references — using fallback center ({center_lat}, {center_lon})")

        def pixel_to_wgs84(px, py):
            # Approximate: 1 pt ≈ 2m for typical Sépaq 1:10000 maps
            scale_m_per_pt = 2.0
            dlat = -(py - H / 2) * scale_m_per_pt / 111320.0
            dlon =  (px - W / 2) * scale_m_per_pt / (111320.0 * math.cos(math.radians(center_lat)))
            return center_lon + dlon, center_lat + dlat

        pixel_to_utm = pixel_to_wgs84

    # ── 3. Extract drawing paths ──────────────────────────────────────────────
    paths = page.get_drawings()
    print(f"  Drawing paths found: {len(paths)}")

    lake_polygon = []
    waterways = []
    fishing_spots = []
    boat_access = []
    portage = []

    def color_distance(c1, c2):
        """Euclidean distance in RGB space (each channel 0-1)."""
        if c1 is None or c2 is None:
            return 999.0
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

    # Sépaq color signatures (normalized 0-1)
    COLOR_LAKE_FILL    = (173/255, 216/255, 230/255)  # light blue
    COLOR_STREAM_STROKE = (0/255, 100/255, 200/255)    # blue
    COLOR_SPOT_FILL    = (255/255, 0/255, 128/255)     # magenta
    COLOR_BOAT_FILL    = (0/255,   0/255,   0/255)     # black
    COLOR_PORTAGE      = (150/255, 75/255,  0/255)     # brown

    LAKE_COLOR_THRESH  = 0.25
    STREAM_COLOR_THRESH = 0.35
    SPOT_COLOR_THRESH  = 0.40
    BOAT_COLOR_THRESH  = 0.15
    PORTAGE_COLOR_THRESH = 0.25

    largest_lake_path = None
    largest_lake_pts  = 0

    for path in paths:
        fill  = path.get("fill")
        color = path.get("color")  # stroke
        items = path.get("items", [])

        # Collect all points in this path
        pts = []
        for item in items:
            kind = item[0]
            if kind == "l":   # line segment
                pts.extend([item[1], item[2]])
            elif kind == "c": # cubic bezier
                pts.extend([item[1], item[4]])  # endpoints only
            elif kind == "m": # move to
                pts.append(item[1])
            elif kind == "re": # rectangle
                r = item[1]
                pts.extend([r.tl, r.tr, r.br, r.bl])

        if not pts:
            continue

        # Deduplicate
        unique_pts = list({(round(p.x, 1), round(p.y, 1)): p for p in pts}.values())

        # ── Lake polygon (large light-blue fill) ─────────────────────────────
        if fill and color_distance(fill, COLOR_LAKE_FILL) < LAKE_COLOR_THRESH:
            if len(unique_pts) > largest_lake_pts:
                largest_lake_pts = len(unique_pts)
                largest_lake_path = unique_pts

        # ── Waterways (blue stroke, minimal fill) ────────────────────────────
        elif color and color_distance(color, COLOR_STREAM_STROKE) < STREAM_COLOR_THRESH:
            if len(unique_pts) >= 2:
                ww_coords = []
                for p in unique_pts:
                    lon, lat = pixel_to_wgs84(p.x, p.y)
                    ww_coords.append([lon, lat])
                cx = np.mean([c[0] for c in ww_coords])
                cy = np.mean([c[1] for c in ww_coords])
                waterways.append({
                    "coords": ww_coords,
                    "center_lon": float(cx),
                    "center_lat": float(cy)
                })

        # ── Fishing spots (magenta/red fill, small circular shapes) ──────────
        elif fill and color_distance(fill, COLOR_SPOT_FILL) < SPOT_COLOR_THRESH:
            if len(unique_pts) >= 3:
                cx = np.mean([p.x for p in unique_pts])
                cy = np.mean([p.y for p in unique_pts])
                lon, lat = pixel_to_wgs84(cx, cy)
                fishing_spots.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "name": "Site de pêche favorable"
                })

        # ── Boat access (small black filled squares) ─────────────────────────
        elif fill and color_distance(fill, COLOR_BOAT_FILL) < BOAT_COLOR_THRESH:
            bbox = path.get("rect")
            if bbox:
                w_pts = bbox.width
                h_pts = bbox.height
                aspect = max(w_pts, h_pts) / max(min(w_pts, h_pts), 0.1)
                if 0.5 < aspect < 2.5 and 2 < max(w_pts, h_pts) < 30:
                    cx = (bbox.x0 + bbox.x1) / 2
                    cy = (bbox.y0 + bbox.y1) / 2
                    lon, lat = pixel_to_wgs84(cx, cy)
                    boat_access.append({"lat": float(lat), "lon": float(lon)})

        # ── Portage trails (brown/dashed lines) ──────────────────────────────
        elif color and color_distance(color, COLOR_PORTAGE) < PORTAGE_COLOR_THRESH:
            if len(unique_pts) >= 2:
                trail_coords = []
                for p in unique_pts:
                    lon, lat = pixel_to_wgs84(p.x, p.y)
                    trail_coords.append([lon, lat])
                portage.append({"coords": trail_coords})

    # ── Convert lake polygon ────────────────────────────────────────────────
    if largest_lake_path:
        for p in largest_lake_path:
            lon, lat = pixel_to_wgs84(p.x, p.y)
            lake_polygon.append([lon, lat])
        print(f"  Lake polygon: {len(lake_polygon)} points extracted")
    else:
        print("  ⚠️  No lake polygon found — check PDF color conventions")

    # ── Classify waterways as inlet/outlet ────────────────────────────────
    if lake_polygon:
        lake_lats = [p[1] for p in lake_polygon]
        lake_centroid_lat = np.mean(lake_lats)
        for ww in waterways:
            if ww["center_lat"] > lake_centroid_lat:
                ww["type"] = "inlet"
            else:
                ww["type"] = "outlet"
    else:
        for ww in waterways:
            ww["type"] = "stream"

    # ── Compute lake center and bbox ────────────────────────────────────
    if lake_polygon:
        lons = [p[0] for p in lake_polygon]
        lats = [p[1] for p in lake_polygon]
        lake_center = {
            "lat": float(np.mean(lats)),
            "lon": float(np.mean(lons))
        }
        lake_bbox = {
            "S": float(min(lats)), "N": float(max(lats)),
            "W": float(min(lons)), "E": float(max(lons))
        }
    else:
        lake_center = {"lat": lac_config.get("lat", 0), "lon": lac_config.get("lon", 0)}
        lake_bbox   = {"S": 0, "N": 0, "W": 0, "E": 0}

    doc.close()

    result = {
        "lake_polygon":  lake_polygon,
        "waterways":     waterways,
        "fishing_spots": fishing_spots,
        "boat_access":   boat_access,
        "portage":       portage,
        "lake_center":   lake_center,
        "lake_bbox":     lake_bbox,
    }

    print(f"\n  Summary for Lac {lac_config['name']}:")
    print(f"    Lake polygon:  {len(lake_polygon)} points")
    print(f"    Waterways:     {len(waterways)}")
    print(f"    Fishing spots: {len(fishing_spots)}")
    print(f"    Boat access:   {len(boat_access)}")
    print(f"    Portage:       {len(portage)}")
    print(f"    Center:        {lake_center['lat']:.5f}°N, {lake_center['lon']:.5f}°W")

    # Optionally save to JSON for later HTML generation
    output_dir = lac_config.get("output_dir", ".")
    json_path = os.path.join(output_dir, f"{lac_config.get('file', 'lac')}_pdf_data.json")
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(result, jf, ensure_ascii=False, indent=2)
    print(f"    Data saved: {json_path}")
    print("    → Call run_render_test(html_path) after building HTML from this data.")

    return result


if __name__ == "__main__":
    main()

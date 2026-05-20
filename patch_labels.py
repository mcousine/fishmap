#!/usr/bin/env python3
"""
Patch curated maps: replace hardcoded DEPTH_LABEL_POS / THERMAL_DEPTH_LABEL_POS
with dynamic _computeLabelPositions() that reads BATHYMETRY_GEOJSON at runtime.
Combined thermal labels show "temp · depth" — no separate tdepth-lbl.
"""
import re, sys
from pathlib import Path

FISHMAP = Path('/Users/michelcousineau/Downloads/fishmap')

CURATED = [
    'lac_fox_peche.html',
    'lac_dore_peche.html',
    'lac_marcel_peche.html',
    'lac_osborn_peche.html',
    'lac_baie_peche.html',
    'lac_chutenoire_peche.html',
    'lac_saules_peche.html',
    'lac_sable_peche.html',
]

NEW_LABEL_BLOCK = '''\
// ============================================================
// DYNAMIC LABEL POSITIONS — computed from BATHYMETRY_GEOJSON
// One position per polygon ring, repulsion-dedup for no overlap
// ============================================================
function _computeLabelPositions() {
  var positions = [];
  var MIN_DIST = 0.0005;
  BATHYMETRY_GEOJSON.features.forEach(function(f) {
    var depth = f.properties.depth_m;
    var geom = f.geometry;
    var polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
    polys.forEach(function(poly) {
      var outer = poly[0];
      var lat = 0, lon = 0;
      outer.forEach(function(c) { lon += c[0]; lat += c[1]; });
      lat /= outer.length; lon /= outer.length;
      positions.push({depth_m: depth, lat: lat, lon: lon});
    });
  });
  for (var iter = 0; iter < 8; iter++) {
    for (var i = 0; i < positions.length; i++) {
      for (var j = i + 1; j < positions.length; j++) {
        var dlat = positions[j].lat - positions[i].lat;
        var dlon = positions[j].lon - positions[i].lon;
        var dist = Math.sqrt(dlat*dlat + dlon*dlon);
        if (dist < MIN_DIST && dist > 0) {
          var push = (MIN_DIST - dist) / 2;
          var nx = dlat/dist, ny = dlon/dist;
          positions[i].lat -= nx*push; positions[i].lon -= ny*push;
          positions[j].lat += nx*push; positions[j].lon += ny*push;
        }
      }
    }
  }
  return positions;
}
var LABEL_POSITIONS = []; // populated in initGeoJSON after BATHYMETRY_GEOJSON loads

function depthVal(d) {
  return currentUnit === 'm' ? d.toFixed(1) + ' m' : (d * 3.28084).toFixed(0) + ' pi';
}

var depthLabelMarkers = [];
function buildDepthLabels() {
  depthLabelMarkers.forEach(function(m) { m.remove(); });
  depthLabelMarkers.length = 0;
  LABEL_POSITIONS.forEach(function(lp) {
    var m = L.marker([lp.lat, lp.lon], {
      icon: L.divIcon({
        className: 'dlabel-icon',
        html: '<span class="dlabel-txt" data-d="' + lp.depth_m + '">' + depthVal(lp.depth_m) + '</span>',
        iconSize: [52, 18], iconAnchor: [26, 9]
      }), interactive: false, zIndexOffset: -100
    });
    m.addTo(layerBathy);
    depthLabelMarkers.push(m);
  });
}
function updateDepthLabels() {
  depthLabelMarkers.forEach(function(m) {
    var el = m.getElement();
    if (!el) return;
    var span = el.querySelector('.dlabel-txt');
    if (span) span.textContent = depthVal(parseFloat(span.dataset.d));
  });
}

var thermalLabelMarkers = [];
function buildThermalLabels() {
  thermalLabelMarkers.forEach(function(m) { m.remove(); });
  thermalLabelMarkers.length = 0;
  LABEL_POSITIONS.forEach(function(lp) {
    var t = getPolygonTemp(lp.depth_m, currentTime);
    var col = getThermalColor(t);
    var m = L.marker([lp.lat, lp.lon], {
      icon: L.divIcon({
        className: 'dlabel-icon',
        html: '<span class="tlabel-txt" style="background:' + col + 'cc;" data-d="' + lp.depth_m + '">' + tempDisp(t) + ' · ' + depthVal(lp.depth_m) + '</span>',
        iconSize: [88, 20], iconAnchor: [44, 10]
      }), interactive: false, zIndexOffset: -100
    });
    m.addTo(layerThermal);
    thermalLabelMarkers.push(m);
  });
}
function updateThermalLabels() {
  thermalLabelMarkers.forEach(function(m) {
    var el = m.getElement();
    if (!el) return;
    var span = el.querySelector('.tlabel-txt');
    if (!span) return;
    var depth = parseFloat(span.dataset.d);
    var t = getPolygonTemp(depth, currentTime);
    var col = getThermalColor(t);
    span.textContent = tempDisp(t) + ' · ' + depthVal(depth);
    span.style.background = col + 'cc';
  });
}'''


def find_block_bounds(lines):
    """Return (start_idx, end_idx) inclusive for the block to replace."""
    # Find the comment separator just before const DEPTH_LABEL_POS
    depth_pos_line = None
    for i, line in enumerate(lines):
        if re.match(r'\s*const DEPTH_LABEL_POS\s*=', line):
            depth_pos_line = i
            break
    if depth_pos_line is None:
        return None, None

    # Walk back to find the start of the comment block (first '// ===' line)
    start = depth_pos_line
    for j in range(depth_pos_line - 1, max(depth_pos_line - 10, -1), -1):
        stripped = lines[j].strip()
        if stripped.startswith('//') or stripped == '':
            start = j
        else:
            break

    # Find the LAST definition of function updateThermalLabels
    func_line = None
    for i, line in enumerate(lines):
        if re.match(r'\s*function updateThermalLabels\s*\(', line):
            func_line = i

    if func_line is None:
        return None, None

    # Count braces from func_line to find closing }
    brace_depth = 0
    end = None
    for i in range(func_line, len(lines)):
        brace_depth += lines[i].count('{') - lines[i].count('}')
        if brace_depth == 0 and i > func_line:
            end = i
            break

    return start, end


def patch_file(path: Path):
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    # 1. Remove duplicate CSS rule: keep only first occurrence
    css_rule = '#map.thermal-on .dlabel-txt'
    seen = False
    new_lines = []
    for line in lines:
        if css_rule in line:
            if not seen:
                seen = True
                new_lines.append(line)
            # else skip duplicate
        else:
            new_lines.append(line)
    lines = new_lines

    # 2. Replace label block
    start, end = find_block_bounds(lines)
    if start is None or end is None:
        print(f'  SKIP: could not find block bounds in {path.name}')
        return False

    # Strip trailing whitespace from new block lines, preserve newlines
    replacement_lines = [(l + '\n') for l in NEW_LABEL_BLOCK.split('\n')]

    lines = lines[:start] + replacement_lines + lines[end + 1:]

    # 3. Remove buildThermalDepthLabels() calls
    new_lines = []
    for line in lines:
        if re.match(r'\s*buildThermalDepthLabels\(\);\s*$', line):
            pass  # drop it
        else:
            new_lines.append(line)
    lines = new_lines

    # 4. Inject LABEL_POSITIONS = _computeLabelPositions() at start of initGeoJSON
    #    so it runs after BATHYMETRY_GEOJSON is defined (const TDZ fix)
    new_lines = []
    for line in lines:
        if re.match(r'\s*\(function initGeoJSON\(\)', line):
            new_lines.append(line)
            # Add the call on the next line (inside the IIFE, before geoJsonLayer)
            indent = '  '
            new_lines.append(indent + 'LABEL_POSITIONS = _computeLabelPositions();\n')
        else:
            new_lines.append(line)
    lines = new_lines

    path.write_text(''.join(lines), encoding='utf-8')
    return True


if __name__ == '__main__':
    targets = sys.argv[1:] if len(sys.argv) > 1 else CURATED
    for name in targets:
        p = FISHMAP / name
        if not p.exists():
            print(f'MISSING: {name}')
            continue
        ok = patch_file(p)
        if ok:
            print(f'PATCHED: {name}')
        else:
            print(f'FAILED:  {name}')

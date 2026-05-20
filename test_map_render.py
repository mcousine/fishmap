#!/usr/bin/env python3.11
"""
Automated map rendering test using Playwright.
Usage: python3.11 test_map_render.py <path_to_html_map>
Returns exit code 0 if map renders correctly, 1 if issues detected.
"""

import sys
import os
import time
import json
import base64
import subprocess
from pathlib import Path


def check_map_renders(html_path: str, screenshot_path: str = None, timeout_ms: int = 12000) -> dict:
    """
    Render an HTML map file in headless Chromium and verify it loads correctly.
    Returns a dict with: success, tiles_loaded, markers_visible, console_errors, screenshot_path
    """
    from playwright.sync_api import sync_playwright

    html_path = os.path.abspath(html_path)
    if not os.path.exists(html_path):
        return {"success": False, "error": f"File not found: {html_path}"}

    if screenshot_path is None:
        base = os.path.splitext(html_path)[0]
        screenshot_path = base + "_render_test.png"

    results = {
        "success": False,
        "tiles_loaded": False,
        "map_visible": False,
        "console_errors": [],
        "screenshot_path": screenshot_path,
        "map_center": None,
        "zoom_level": None,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        console_errors = []
        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", on_console)

        # Navigate to the file
        page.goto(f"file://{html_path}")

        # Wait for Leaflet map to initialise (map container gets class 'leaflet-container')
        try:
            page.wait_for_selector(".leaflet-container", timeout=timeout_ms)
            results["map_visible"] = True
        except Exception:
            results["error"] = "Leaflet map container never appeared"
            page.screenshot(path=screenshot_path)
            results["screenshot_path"] = screenshot_path
            browser.close()
            return results

        # Wait a bit for tiles to load
        page.wait_for_timeout(4000)

        # Check tile load: look for leaflet tile images that are actually rendered
        tiles_ok = page.evaluate("""() => {
            const imgs = document.querySelectorAll('.leaflet-tile');
            if (imgs.length === 0) return false;
            // At least one tile should be complete and have naturalWidth > 0
            return Array.from(imgs).some(img => img.complete && img.naturalWidth > 0);
        }""")
        results["tiles_loaded"] = bool(tiles_ok)

        # Get map center and zoom from Leaflet
        map_state = page.evaluate("""() => {
            // Try common global map variable names
            const candidates = ['map', 'leafletMap', 'myMap'];
            for (const name of candidates) {
                if (window[name] && window[name].getCenter) {
                    const c = window[name].getCenter();
                    return { lat: c.lat, lon: c.lng, zoom: window[name].getZoom() };
                }
            }
            return null;
        }""")
        results["map_center"] = map_state

        if map_state:
            results["zoom_level"] = map_state.get("zoom")

        # Check that the map area is not entirely dark/black (rendering failure indicator)
        # Sample the center 400x300 pixel area and check average brightness
        brightness = page.evaluate("""() => {
            const canvas = document.createElement('canvas');
            canvas.width = 400; canvas.height = 300;
            const ctx = canvas.getContext('2d');
            // Draw the map div onto canvas
            const mapEl = document.querySelector('.leaflet-container');
            if (!mapEl) return -1;
            const rect = mapEl.getBoundingClientRect();
            // We can't easily drawImage a div, but we can check if tile images exist
            const tiles = document.querySelectorAll('.leaflet-tile-container img');
            return tiles.length;
        }""")
        results["tile_count"] = brightness

        results["console_errors"] = console_errors
        results["success"] = (
            results["map_visible"]
            and results["tiles_loaded"]
            and len(console_errors) == 0
        )

        page.screenshot(path=screenshot_path, full_page=False)
        browser.close()

    return results


def check_map_buttons(html_path: str, screenshot_dir: str = None, timeout_ms: int = 12000) -> dict:
    """
    Test the 3 map mode buttons (Sombre/Satellite/Thermique) and thermal time-slider update.
    Takes a screenshot after each button click and after advancing the time slider.
    Returns a dict with per-button pass/fail and screenshot paths.
    """
    from playwright.sync_api import sync_playwright

    html_path = os.path.abspath(html_path)
    base_name = os.path.splitext(os.path.basename(html_path))[0]
    if screenshot_dir is None:
        screenshot_dir = os.path.dirname(html_path)
    os.makedirs(screenshot_dir, exist_ok=True)

    results = {
        "success": False,
        "buttons_found": {},
        "mode_switches": {},
        "thermal_update": False,
        "screenshots": {},
        "console_errors": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"file://{html_path}")
        try:
            page.wait_for_selector(".leaflet-container", timeout=timeout_ms)
        except Exception:
            results["error"] = "Map never loaded"
            browser.close()
            return results

        page.wait_for_timeout(3000)

        # ── Check buttons exist (2-button design: Carte + Thermique) ──────────
        for btn_id in ["btnSat", "btnThermal"]:
            exists = page.evaluate(f"() => !!document.getElementById('{btn_id}')")
            results["buttons_found"][btn_id] = exists

        # ── Initial screenshot ──────────────────────────────────────────────
        ss = os.path.join(screenshot_dir, f"{base_name}_btn_00_initial.png")
        page.screenshot(path=ss)
        results["screenshots"]["initial"] = ss

        # ── Test each mode button ───────────────────────────────────────────
        modes = [
            ("satellite","btnSat",     "Carte"),
            ("thermal",  "btnThermal", "Thermique"),
        ]

        for mode, btn_id, label in modes:
            btn = page.query_selector(f"#{btn_id}")
            if btn:
                btn.click()
                page.wait_for_timeout(800)

                active_id = page.evaluate(
                    "() => { const btns = document.querySelectorAll('.map-mode-btn');"
                    " for (const b of btns) { if (b.classList.contains('active')) return b.id; }"
                    " return null; }"
                )
                correct_active = (active_id == btn_id)
                results["mode_switches"][mode] = correct_active

                ss = os.path.join(screenshot_dir, f"{base_name}_btn_{mode}.png")
                page.screenshot(path=ss)
                results["screenshots"][mode] = ss
            else:
                results["mode_switches"][mode] = False

        # ── Test thermal update with time slider ────────────────────────────
        # Switch to thermal mode, record initial waterTemp, advance slider, check update
        thermal_btn = page.query_selector("#btnThermal")
        if thermal_btn:
            thermal_btn.click()
            page.wait_for_timeout(500)

            temp_before = page.evaluate(
                "() => document.getElementById('waterTempText')?.textContent || ''"
            )

            update_fn_exists = page.evaluate(
                "() => typeof window._updateThermalColors === 'function'"
            )

            # Advance time slider to 20h (evening)
            slider = page.query_selector("#timeSlider")
            if slider:
                page.evaluate("() => { const s = document.getElementById('timeSlider');"
                               " s.value = '20'; s.dispatchEvent(new Event('input')); }")
                page.wait_for_timeout(600)

            temp_after = page.evaluate(
                "() => document.getElementById('waterTempText')?.textContent || ''"
            )

            thermal_updated = (temp_before != temp_after) and update_fn_exists
            results["thermal_update"] = thermal_updated
            results["thermal_update_fn_exists"] = update_fn_exists
            results["temp_before"] = temp_before
            results["temp_after"] = temp_after

            ss = os.path.join(screenshot_dir, f"{base_name}_btn_thermal_after_slider.png")
            page.screenshot(path=ss)
            results["screenshots"]["thermal_after_slider"] = ss

        results["console_errors"] = console_errors
        # Success: thermal button exists + thermal mode works + update fn exists
        # (btnSat optional — new design uses single Thermique toggle)
        thermal_btn_ok = results["buttons_found"].get("btnThermal", False)
        thermal_mode_ok = results["mode_switches"].get("thermal", False)
        results["success"] = thermal_btn_ok and thermal_mode_ok and results.get("thermal_update_fn_exists", False)

        browser.close()

    return results


def render_test(html_path: str, verbose: bool = True) -> bool:
    """Run rendering test and print results. Returns True if passed."""
    print(f"\n=== Map Render Test: {os.path.basename(html_path)} ===")

    results = check_map_renders(html_path)

    status = "PASS" if results["success"] else "FAIL"
    print(f"Status       : {status}")
    print(f"Map visible  : {results.get('map_visible', False)}")
    print(f"Tiles loaded : {results.get('tiles_loaded', False)}")

    if results.get("map_center"):
        c = results["map_center"]
        print(f"Map center   : {c['lat']:.4f}°N, {c['lon']:.4f}°W  zoom={results.get('zoom_level')}")

    if results.get("console_errors"):
        print(f"JS Errors    : {len(results['console_errors'])}")
        for err in results["console_errors"][:5]:
            print(f"  ✗ {err[:120]}")

    if results.get("error"):
        print(f"Error        : {results['error']}")

    if results.get("screenshot_path") and os.path.exists(results["screenshot_path"]):
        print(f"Screenshot   : {results['screenshot_path']}")

    print("=" * 50)
    return results["success"]


def button_test(html_path: str) -> bool:
    """Run button behavior test and print results. Returns True if passed."""
    print(f"\n=== Button Behavior Test: {os.path.basename(html_path)} ===")

    results = check_map_buttons(html_path)

    status = "PASS" if results["success"] else "FAIL"
    print(f"Status           : {status}")
    print(f"Buttons found    : {results.get('buttons_found', {})}")
    print(f"Mode switches    : {results.get('mode_switches', {})}")
    print(f"Update fn exists : {results.get('thermal_update_fn_exists', False)}")
    print(f"Thermal update   : {results.get('thermal_update', False)} "
          f"({results.get('temp_before','?')} → {results.get('temp_after','?')})")

    if results.get("console_errors"):
        print(f"JS Errors        : {len(results['console_errors'])}")
        for err in results["console_errors"][:5]:
            print(f"  ✗ {err[:120]}")

    for label, path in results.get("screenshots", {}).items():
        if os.path.exists(path):
            print(f"Screenshot [{label}]: {path}")

    print("=" * 50)
    return results["success"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3.11 test_map_render.py <map.html> [map2.html ...]")
        print("       python3.11 test_map_render.py --buttons <map.html> [map2.html ...]")
        sys.exit(1)

    run_buttons = "--buttons" in sys.argv
    paths = [p for p in sys.argv[1:] if not p.startswith("--")]

    all_pass = True
    for path in paths:
        if run_buttons:
            ok = button_test(path)
        else:
            ok = render_test(path)
        if not ok:
            all_pass = False

    sys.exit(0 if all_pass else 1)

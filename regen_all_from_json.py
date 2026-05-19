#!/usr/bin/env python3
"""Regenerate all lake HTML maps from their JSON cache files.

Skips the 8 curated maps that were fixed manually.
"""

import sys
import os
import glob
import json
import unicodedata

# Make gen_sdb importable
sys.path.insert(0, '/Users/michelcousineau/Downloads/fishmap')
import gen_sdb
from gen_sdb import build_pdf_html, MASTIGOUCHE_STATS, _lake_stats

FISHMAP_DIR   = '/Users/michelcousineau/Downloads/fishmap'
TEMPLATE_PATH = '/Users/michelcousineau/Downloads/fishmap/lac_romeo_peche.html'
TRIP_DATES    = ["2026-05-20", "2026-05-21", "2026-05-22"]

# Maps that were already fixed manually — do NOT overwrite them
CURATED = {
    "lac_fox_peche.html",
    "lac_dore_peche.html",
    "lac_marcel_peche.html",
    "lac_osborn_peche.html",
    "lac_baie_peche.html",
    "lac_chutenoire_peche.html",
    "lac_saules_peche.html",
    "lac_sable_peche.html",
}

# Special-case overrides: JSON slug → exact lac_name to use
SLUG_OVERRIDES = {
    "lac_mas_carte_grand_lac_des_iles": "Grand lac des Îles",
    "lac_grand_lac_des_iles":           "Grand lac des Îles",
}


def slugify(name: str) -> str:
    """'Lac du Hêtre' → 'lac_du_hetre'  (same logic as batch_pdf_maps)"""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return ascii_str.lower().replace(" ", "_").replace("'", "_").replace("-", "_")


def slug_to_lac_name(file_slug: str) -> str:
    """Try to map a file slug (e.g. 'lac_du_soufflet') to the proper lake name.

    Strategy:
    1. Hard-coded overrides
    2. Strip leading 'lac_' to get the name part, then try to match against
       MASTIGOUCHE_STATS keys via their slugified forms
    3. Fallback: capitalise the name part
    """
    # 1. Overrides
    if file_slug in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[file_slug]

    # 2. Strip 'lac_' prefix to get candidate name part
    if file_slug.startswith("lac_"):
        name_part_slug = file_slug[4:]   # e.g. "du_soufflet"
    else:
        name_part_slug = file_slug

    # Build a normalised slug for every key in MASTIGOUCHE_STATS
    def _norm_slug(s):
        nfkd = unicodedata.normalize("NFKD", s)
        return nfkd.encode("ascii", "ignore").decode("ascii").lower() \
                   .replace(" ", "_").replace("'", "_").replace("-", "_")

    # Exact match first
    for k in MASTIGOUCHE_STATS:
        if _norm_slug(k) == name_part_slug:
            return k

    # Partial match (slug contains key-slug or vice-versa)
    for k in MASTIGOUCHE_STATS:
        nk = _norm_slug(k)
        if name_part_slug in nk or nk in name_part_slug:
            return k

    # 3. Fallback: capitalise the name part (underscores → spaces)
    fallback = name_part_slug.replace("_", " ").title()
    return fallback


def main():
    json_files = sorted(glob.glob(os.path.join(FISHMAP_DIR, '*_pdf_data.json')))
    print(f"Found {len(json_files)} JSON cache files.")

    skipped_curated  = []
    skipped_unnamed  = []
    processed_ok     = []
    processed_errors = []

    for json_path in json_files:
        basename   = os.path.basename(json_path)              # e.g. lac_victoire_pdf_data.json
        file_slug  = basename.replace('_pdf_data.json', '')   # e.g. lac_victoire
        html_name  = file_slug + '_peche.html'                # e.g. lac_victoire_peche.html
        html_path  = os.path.join(FISHMAP_DIR, html_name)

        # Skip the unnamed test file
        if file_slug == 'lac':
            print(f"  [SKIP-unnamed]  {basename}")
            skipped_unnamed.append(basename)
            continue

        # Skip curated maps
        if html_name in CURATED:
            print(f"  [SKIP-curated]  {html_name}")
            skipped_curated.append(html_name)
            continue

        # Resolve lake name
        lac_name = slug_to_lac_name(file_slug)

        # Get stats (build_pdf_html calls _lake_stats internally, but we also
        # use them for vehicule / portage_min defaults in lac_config)
        st = _lake_stats(lac_name)

        lac_config = {
            "name":        lac_name,
            "file":        file_slug,
            "output_dir":  FISHMAP_DIR,
            "species":     "Omble de fontaine",
            "trip_dates":  TRIP_DATES,
            "vehicule":    st.get("vehicule", "Auto"),
            "portage_min": st.get("portage_min", 0),
        }

        # Load JSON data
        try:
            with open(json_path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [ERROR-json]    {basename}: {e}")
            processed_errors.append((basename, str(e)))
            continue

        # Generate HTML
        try:
            html = build_pdf_html(data, lac_config, TEMPLATE_PATH)
        except Exception as e:
            print(f"  [ERROR-build]   {html_name}: {e}")
            processed_errors.append((html_name, str(e)))
            continue

        # Write HTML
        try:
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"  [OK]  {html_name:45s}  (lac: {lac_name})")
            processed_ok.append(html_name)
        except Exception as e:
            print(f"  [ERROR-write]   {html_name}: {e}")
            processed_errors.append((html_name, str(e)))

    # Summary
    print()
    print("=" * 60)
    print(f"  Done.")
    print(f"  Generated:       {len(processed_ok)}")
    print(f"  Skipped curated: {len(skipped_curated)}")
    print(f"  Skipped unnamed: {len(skipped_unnamed)}")
    print(f"  Errors:          {len(processed_errors)}")
    if processed_errors:
        print()
        print("  Errors detail:")
        for name, err in processed_errors:
            print(f"    {name}: {err}")
    print("=" * 60)


if __name__ == "__main__":
    main()

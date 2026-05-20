#!/usr/bin/env python3
"""
FishMap v4 upgrade — patches all HTML files (except lac_chutenoire_peche.html).

Changes applied:
  1. CSS  — wx-mini (weather collapse), compact date chip, compact time after date pick
  2. JS   — weather widget click → toggle wx-mini
  3. JS   — map.on('zoomend', update) to refresh labels after zoom
  4. JS   — setDate() upgraded with compact-chip + ctrl-date-picked time shrink
  5. GPS  — _mobileSetup → _simSetup: remove mobile guard, add _debugPanel reparent fix
"""
import re, sys
from pathlib import Path

FISHMAP = Path('/Users/michelcousineau/Downloads/fishmap')

# ─── 1. CSS block to inject ──────────────────────────────────────────────────
NEW_CSS = '''\
<style>
/* v4: weather mini, compact date chip, compact time */
#weatherWidget{cursor:pointer}
.weather-widget.wx-compact .weather-temp,.weather-widget.wx-compact .weather-desc{display:none}
.weather-widget.wx-compact{padding:4px 8px;gap:4px}
.weather-widget.wx-mini{background:transparent!important;border-color:transparent!important;padding:2px 4px!important;box-shadow:none!important}
.weather-widget.wx-mini .weather-info{display:none}
.weather-widget.wx-mini .weather-icon{font-size:22px;opacity:.8}
#dateBtns.date-collapsed .date-btn:not(.active){display:none}
#dateBtns.date-collapsed .date-btn.active{padding:3px 8px;font-size:10px;border-radius:6px}
.date-group-compact>label{font-size:9px;letter-spacing:.3px}
.ctrl-date-picked .time-display{font-size:13px!important;min-width:28px!important}
.ctrl-date-picked .control-group>label{font-size:7.5px!important;letter-spacing:0!important}
.ctrl-date-picked #timeSlider{width:80px!important}
</style>
'''

# ─── 2. New setDate() function ────────────────────────────────────────────────
NEW_SETDATE = '''\
function setDate(btn) {
  var container = document.getElementById('dateBtns');
  var ctrl = document.querySelector('.control-panel');
  if (container.classList.contains('date-collapsed') && btn.classList.contains('active')) {
    container.classList.remove('date-collapsed');
    btn.closest('.control-group').classList.remove('date-group-compact');
    if (ctrl) ctrl.classList.remove('ctrl-date-picked');
    return;
  }
  document.querySelectorAll('.date-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  currentDate = btn.dataset.date;
  currentDayIdx = parseInt(btn.dataset.idx || 0);
  update();
  setTimeout(function() {
    container.classList.add('date-collapsed');
    var grp = btn.closest('.control-group');
    if (grp) grp.classList.add('date-group-compact');
    if (ctrl) ctrl.classList.add('ctrl-date-picked');
  }, 200);
}'''

# ─── 3. JS to inject after initial update() call ─────────────────────────────
NEW_JS_AFTER_UPDATE = '''\

// v4: weather mini toggle
(function(){
  var wx=document.getElementById('weatherWidget');
  if(wx)wx.addEventListener('click',function(){wx.classList.toggle('wx-mini');});
})();
// v4: refresh labels/markers after zoom
map.on('zoomend',function(){if(typeof update==='function')update();});
'''

# ─── 4. New _simSetup replacing _mobileSetup ─────────────────────────────────
NEW_SIM_SETUP = '''\
  /* ── Auto-collapse sim + 🔧 toggle button (all screens) ── */
  (function _simSetup(){
    setTimeout(function(){
      if(window.matchMedia('(max-width:768px)').matches){
        var body=document.getElementById('_debugBody');
        if(body&&body.style.display!=='none'&&typeof window._dbToggle==='function'){
          window._dbToggle();
        }
      }
      if(!document.getElementById('btnSimToggle')){
        var sb=document.createElement('button');
        sb.id='btnSimToggle';sb.title='Simulateur météo';sb.textContent='🔧';
        sb.style.cssText='position:fixed;bottom:144px;left:8px;z-index:1001;width:36px;height:36px;'
          +'background:rgba(10,14,26,.92);border:1.5px solid rgba(148,163,184,.25);border-radius:8px;'
          +'font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;'
          +'backdrop-filter:blur(6px);box-shadow:0 2px 8px rgba(0,0,0,.45)';
        sb.addEventListener('click',function(){
          var p=document.getElementById('_debugPanel');
          if(!p)return;
          if(p.parentElement&&p.parentElement.id==='sunIndicator'){
            document.body.appendChild(p);
            p.style.cssText='position:fixed;bottom:80px;left:8px;right:8px;z-index:1002;'
              +'background:rgba(10,14,26,.97);border:1px solid rgba(148,163,184,.25);'
              +'border-radius:10px;padding:8px;backdrop-filter:blur(16px);'
              +'-webkit-backdrop-filter:blur(16px);display:block;';
            sb.style.borderColor='#f59e0b';
            return;
          }
          var visible=p.style.display!=='none';
          p.style.display=visible?'none':'';
          sb.style.borderColor=visible?'rgba(148,163,184,.25)':'#f59e0b';
        });
        document.body.appendChild(sb);
      }
    },750);
  })();'''

# ─── Regex patterns ───────────────────────────────────────────────────────────

SETDATE_RE = re.compile(
    r'function setDate\(btn\)\s*\{[^}]+\}',
    re.DOTALL
)

MOBILESETUP_RE = re.compile(
    r'/\* ── Mobile: auto-collapse sim \+ 🔧 toggle button.*?\}\)\(\);',
    re.DOTALL
)

# Anchor: the GPS v3 <style> block
GPS_STYLE_ANCHOR = '<style>\n/* GPS PWA v3 */'

# Anchor: after initial update() call followed by leaflet popup style
UPDATE_ANCHOR_RE = re.compile(
    r'(document\.getElementById\(\'timeSlider\'\)\.value = currentTime;\nupdate\(\);)\n'
)


def patch_file(path: Path) -> str:
    text = path.read_text(encoding='utf-8')

    # Skip if already patched (v4 marker)
    if '/* v4:' in text or '_simSetup' in text:
        return 'already_v4'

    changed = False

    # ── 1. Inject CSS before GPS v3 style block ───────────────────────────────
    if GPS_STYLE_ANCHOR in text:
        text = text.replace(GPS_STYLE_ANCHOR, NEW_CSS + GPS_STYLE_ANCHOR, 1)
        changed = True
    else:
        return 'no_gps_block'

    # ── 2. Replace setDate() ──────────────────────────────────────────────────
    new_text, n = SETDATE_RE.subn(NEW_SETDATE, text, count=1)
    if n:
        text = new_text
    # (not blocking if not found — some maps may differ)

    # ── 3. Inject weather handler + zoomend after update() ───────────────────
    m = UPDATE_ANCHOR_RE.search(text)
    if m:
        insert_pos = m.end()
        text = text[:insert_pos] + NEW_JS_AFTER_UPDATE + text[insert_pos:]

    # ── 4. Replace _mobileSetup with _simSetup ────────────────────────────────
    new_text, n = MOBILESETUP_RE.subn(NEW_SIM_SETUP, text, count=1)
    if n:
        text = new_text
    else:
        # Fallback: broader match
        broader = re.compile(
            r'/\* ── Mobile: auto-collapse sim.*?\}\)\(\);',
            re.DOTALL
        )
        new_text, n = broader.subn(NEW_SIM_SETUP, text, count=1)
        if n:
            text = new_text

    if not changed:
        return 'no_change'

    path.write_text(text, encoding='utf-8')
    return 'patched'


if __name__ == '__main__':
    SKIP = {'lac_chutenoire_peche.html'}
    targets = sys.argv[1:] if len(sys.argv) > 1 else None

    if targets:
        files = [FISHMAP / t for t in targets]
    else:
        files = sorted(f for f in FISHMAP.glob('*.html') if f.name not in SKIP)

    counts = {'patched': 0, 'already_v4': 0, 'no_gps_block': 0, 'no_change': 0}
    for p in files:
        result = patch_file(p)
        counts[result] += 1
        if result != 'already_v4':
            icon = {'patched': 'OK ', 'no_gps_block': '?? ', 'no_change': '-- '}[result]
            print(f'  [{icon}] {p.name}  ({result})')

    print(f'\nDone. patched={counts["patched"]}  already_v4={counts["already_v4"]}  no_gps={counts["no_gps_block"]}')

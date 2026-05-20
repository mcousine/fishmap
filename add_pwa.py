#!/usr/bin/env python3.11
"""
Patch all fishmap HTML files with PWA support:
  - <link rel="manifest"> + Apple meta tags in <head>
  - GPS Leaflet control (pulsing marker, watchPosition)
  - Service Worker registration
  - Handles both lake maps (have `map` Leaflet object) and index.html
"""
import re, sys
from pathlib import Path

FISHMAP = Path('/Users/michelcousineau/Downloads/fishmap')

# ── Blocs à injecter ─────────────────────────────────────────────────────────

PWA_HEAD = '''\
  <link rel="manifest" href="./manifest.json">
  <meta name="theme-color" content="#06b6d4">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="FishMap">
  <link rel="apple-touch-icon" href="./icon-192.png">'''

GPS_CSS = '''\
<style>
/* GPS PWA */
.gps-ctrl-btn{width:36px;height:36px;background:rgba(10,14,26,.92);border:1.5px solid rgba(148,163,184,.25);
  border-radius:8px;color:#f1f5f9;font-size:18px;cursor:pointer;display:flex;align-items:center;
  justify-content:center;backdrop-filter:blur(6px);box-shadow:0 2px 8px rgba(0,0,0,.45);transition:border-color .2s,color .2s}
.gps-ctrl-btn.gps-on{border-color:#06b6d4;color:#06b6d4}
.gps-ctrl-btn.gps-err{border-color:#ef4444;color:#ef4444}
.gps-pulse-wrap{width:18px;height:18px;position:relative}
.gps-pulse-dot{position:absolute;top:2px;left:2px;width:14px;height:14px;background:#06b6d4;
  border-radius:50%;border:2px solid #fff;
  animation:gpsRing 1.6s ease-out infinite}
@keyframes gpsRing{
  0%{box-shadow:0 0 0 0 rgba(6,182,212,.7)}
  70%{box-shadow:0 0 0 10px rgba(6,182,212,0)}
  100%{box-shadow:0 0 0 0 rgba(6,182,212,0)}}
</style>'''

GPS_JS = '''\
<script>
/* ── GPS tracker ────────────────────────────────────────────────── */
(function(){
  if (typeof L === 'undefined' || typeof map === 'undefined') return;
  var _wid=null, _mk=null, _ci=null;

  var ctrl = L.control({position:'bottomright'});
  ctrl.onAdd = function(){
    var d = L.DomUtil.create('div','leaflet-bar');
    d.style.cssText='margin-bottom:8px';
    d.innerHTML='<button id="btnGps" class="gps-ctrl-btn" title="Ma position GPS">📍</button>';
    L.DomEvent.disableClickPropagation(d);
    return d;
  };
  ctrl.addTo(map);

  document.getElementById('btnGps').addEventListener('click',function(){
    var btn=this;
    if(_wid!==null){
      navigator.geolocation.clearWatch(_wid); _wid=null;
      if(_mk){map.removeLayer(_mk);_mk=null;}
      if(_ci){map.removeLayer(_ci);_ci=null;}
      btn.className='gps-ctrl-btn'; btn.textContent='📍';
    } else {
      btn.textContent='⌛';
      _wid=navigator.geolocation.watchPosition(function(p){
        var ll=[p.coords.latitude,p.coords.longitude];
        btn.className='gps-ctrl-btn gps-on'; btn.textContent='📍';
        if(!_mk){
          _mk=L.marker(ll,{icon:L.divIcon({className:'',
            html:'<div class="gps-pulse-wrap"><div class="gps-pulse-dot"></div></div>',
            iconSize:[18,18],iconAnchor:[9,9]}),zIndexOffset:1000}).addTo(map);
          _ci=L.circle(ll,{radius:p.coords.accuracy,
            color:'#06b6d4',fillColor:'#06b6d4',fillOpacity:.1,weight:1}).addTo(map);
          map.panTo(ll);
        } else {
          _mk.setLatLng(ll);
          _ci.setLatLng(ll).setRadius(p.coords.accuracy);
        }
      },function(){
        btn.className='gps-ctrl-btn gps-err'; btn.textContent='📍';
        setTimeout(function(){btn.className='gps-ctrl-btn';},3500);
        _wid=null;
      },{enableHighAccuracy:true,timeout:15000,maximumAge:0});
    }
  });
})();
</script>'''

SW_REG = '''\
<script>
if('serviceWorker'in navigator){
  navigator.serviceWorker.register('./sw.js',{scope:'./'})
    .catch(function(){});
}
</script>'''

# ── Index-page additions ─────────────────────────────────────────────────────

INDEX_INSTALL_CSS = '''\
<style>
/* PWA Install + Offline */
#pwaBar{display:none;align-items:center;gap:12px;padding:12px 20px;
  background:rgba(6,182,212,.12);border:1px solid rgba(6,182,212,.3);
  border-radius:12px;margin-bottom:24px;font-size:13px;color:#f1f5f9}
#pwaBar.show{display:flex}
#pwaBar button{background:linear-gradient(135deg,#06b6d4,#3b82f6);color:#fff;
  border:none;border-radius:8px;padding:7px 18px;font-size:13px;font-weight:700;cursor:pointer}
#dlBar{background:rgba(10,14,26,.8);border:1px solid rgba(148,163,184,.15);
  border-radius:12px;padding:16px 20px;margin-bottom:24px;font-size:13px}
#dlBar h3{font-size:14px;font-weight:700;color:#f1f5f9;margin-bottom:10px}
#dlProgress{height:6px;background:rgba(148,163,184,.15);border-radius:3px;margin:10px 0;overflow:hidden}
#dlProgressFill{height:100%;width:0;background:linear-gradient(90deg,#06b6d4,#3b82f6);
  border-radius:3px;transition:width .3s}
#dlStatus{color:#94a3b8;font-size:12px}
#btnDlAll{background:linear-gradient(135deg,#06b6d4,#3b82f6);color:#fff;
  border:none;border-radius:8px;padding:8px 20px;font-size:13px;font-weight:700;
  cursor:pointer;margin-top:4px}
#btnDlAll:disabled{opacity:.5;cursor:not-allowed}
</style>'''

INDEX_INSTALL_HTML = '''\
<div id="pwaBar">
  <span>📲 Installez FishMap sur votre téléphone pour un accès hors-ligne</span>
  <button id="btnInstall">Installer</button>
</div>
<div id="dlBar">
  <h3>📥 Cartes hors-ligne</h3>
  <div id="dlStatus">Cliquez pour télécharger les 69 cartes de lacs (utilisation du Wi-Fi recommandée).</div>
  <div id="dlProgress"><div id="dlProgressFill"></div></div>
  <button id="btnDlAll">⬇️ Télécharger toutes les cartes</button>
</div>'''

INDEX_JS = '''\
<script>
/* ── PWA install prompt ────────────────────────────────────────────── */
var _deferredPrompt = null;
window.addEventListener('beforeinstallprompt', function(e){
  e.preventDefault(); _deferredPrompt = e;
  document.getElementById('pwaBar').classList.add('show');
});
document.getElementById('btnInstall').addEventListener('click',function(){
  if(_deferredPrompt){_deferredPrompt.prompt();
    _deferredPrompt.userChoice.then(function(){_deferredPrompt=null;
      document.getElementById('pwaBar').classList.remove('show');});
  }
});
window.addEventListener('appinstalled',function(){
  document.getElementById('pwaBar').classList.remove('show');
});

/* ── Download all maps via SW message ─────────────────────────────── */
document.getElementById('btnDlAll').addEventListener('click',function(){
  var btn=this, fill=document.getElementById('dlProgressFill'),
      status=document.getElementById('dlStatus');
  if(!('serviceWorker' in navigator)){
    status.textContent='❌ Service Worker non supporté sur ce navigateur.'; return;
  }
  btn.disabled=true; btn.textContent='⏳ Téléchargement…';
  navigator.serviceWorker.ready.then(function(reg){
    var ch = new MessageChannel();
    ch.port1.onmessage = function(ev){
      var d=ev.data;
      fill.style.width = (d.done/d.total*100)+'%';
      status.textContent = d.complete
        ? '✅ '+d.total+' cartes disponibles hors-ligne !'
        : '⬇️  '+d.done+' / '+d.total+' — '+d.url.replace('./','');
      if(d.complete){ btn.textContent='✅ Terminé'; btn.disabled=false; }
    };
    reg.active.postMessage({type:'CACHE_ALL_MAPS'},[ch.port2]);
  });
});

/* ── SW registration ────────────────────────────────────────────── */
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('./sw.js',{scope:'./'})
    .catch(function(){});
}
</script>'''


# ── Helpers ───────────────────────────────────────────────────────────────────

def already_patched(text):
    return 'rel="manifest"' in text or "serviceWorker.register('./sw.js')" in text


def patch_map(path: Path) -> bool:
    """Add PWA head tags, GPS control, SW registration to a lake map."""
    text = path.read_text(encoding='utf-8')
    if already_patched(text):
        return False  # skip

    # 1. Inject after first <head> or <meta charset>
    head_anchor = re.search(r'(<meta\s[^>]*charset[^>]*>)', text, re.I)
    if head_anchor:
        text = text[:head_anchor.end()] + '\n' + PWA_HEAD + text[head_anchor.end():]
    elif '<head>' in text:
        text = text.replace('<head>', '<head>\n' + PWA_HEAD, 1)
    else:
        return False

    # 2. GPS CSS — inject before first </style> that's NOT inside a script
    #    (just prepend to <body> as separate <style> block)
    if '</body>' in text:
        text = text.replace('</body>', GPS_CSS + '\n' + GPS_JS + '\n' + SW_REG + '\n</body>', 1)
    else:
        text += '\n' + GPS_CSS + '\n' + GPS_JS + '\n' + SW_REG

    path.write_text(text, encoding='utf-8')
    return True


def patch_index(path: Path) -> bool:
    """Add PWA install prompt, offline download, SW registration to index.html."""
    text = path.read_text(encoding='utf-8')
    if already_patched(text):
        return False

    # Head meta
    head_anchor = re.search(r'(<meta\s[^>]*charset[^>]*>)', text, re.I)
    if head_anchor:
        text = text[:head_anchor.end()] + '\n' + PWA_HEAD + text[head_anchor.end():]

    # Install CSS just before </head>
    text = text.replace('</head>', INDEX_INSTALL_CSS + '\n</head>', 1)

    # Install HTML div + download div at top of main content
    # Inject after <div class="page"> or similar opening
    page_div = re.search(r'(<(?:div|main)[^>]*class="page"[^>]*>)', text)
    if page_div:
        text = text[:page_div.end()] + '\n' + INDEX_INSTALL_HTML + text[page_div.end():]

    # SW + install JS before </body>
    text = text.replace('</body>', INDEX_JS + '\n</body>', 1)

    path.write_text(text, encoding='utf-8')
    return True


if __name__ == '__main__':
    patched_maps = 0
    skipped = 0

    # Patch index.html
    idx = FISHMAP / 'index.html'
    if idx.exists():
        ok = patch_index(idx)
        print(f"{'PATCHED' if ok else 'SKIP   '}: index.html")

    # Patch all lake maps
    for p in sorted(FISHMAP.glob('lac_*_peche.html')):
        ok = patch_map(p)
        if ok:
            patched_maps += 1
            print(f'PATCHED: {p.name}')
        else:
            skipped += 1

    print(f'\nDone. Maps patched: {patched_maps}, skipped (already done): {skipped}')

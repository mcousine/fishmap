#!/usr/bin/env python3.11
"""
PWA v2 upgrade — patches all 70 HTML files.
Replaces old GPS block with new one that adds:
  - Offline button (📥) per map
  - Click lake name (h1) to recenter map
  - GPS simulation via debug panel checkbox
  - Sun indicator toggle on mobile
  - Responsive topbar (mode/unit toggles visible via scroll on mobile)
"""
import re, sys
from pathlib import Path

FISHMAP = Path('/Users/michelcousineau/Downloads/fishmap')

# ── New GPS/PWA block ─────────────────────────────────────────────────────────
# The literal emoji chars are intentional and valid in Python 3 source files.

NEW_GPS_BLOCK = '''\
<style>
/* GPS PWA v2 */
.gps-ctrl-btn{width:36px;height:36px;background:rgba(10,14,26,.92);border:1.5px solid rgba(148,163,184,.25);border-radius:8px;color:#f1f5f9;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px);box-shadow:0 2px 8px rgba(0,0,0,.45);transition:border-color .2s,color .2s}
.gps-ctrl-btn.gps-on{border-color:#06b6d4;color:#06b6d4}
.gps-ctrl-btn.gps-err{border-color:#ef4444;color:#ef4444}
.gps-pulse-wrap{width:18px;height:18px;position:relative}
.gps-pulse-dot{position:absolute;top:2px;left:2px;width:14px;height:14px;background:#06b6d4;border-radius:50%;border:2px solid #fff;animation:gpsRing 1.6s ease-out infinite}
@keyframes gpsRing{0%{box-shadow:0 0 0 0 rgba(6,182,212,.7)}70%{box-shadow:0 0 0 10px rgba(6,182,212,0)}100%{box-shadow:0 0 0 0 rgba(6,182,212,0)}}
.top-bar h1{cursor:pointer;user-select:none}
.top-bar h1:active{opacity:.75}
</style>
<script>
(function(){
  if(typeof L==='undefined'||typeof map==='undefined')return;
  var _wid=null,_mk=null,_ci=null,_simMk=null;

  /* ── Controls: offline + GPS ─────────────────────────────────── */
  var ctrl=L.control({position:'bottomright'});
  ctrl.onAdd=function(){
    var wrap=L.DomUtil.create('div');
    wrap.style.cssText='display:flex;flex-direction:column;gap:6px;margin-bottom:8px';
    /* Offline button */
    var off=document.createElement('button');
    off.id='btnOffline';off.className='gps-ctrl-btn';off.title='Rendre disponible hors-ligne';off.textContent='📥';
    L.DomEvent.disableClickPropagation(off);
    off.addEventListener('click',function(){
      off.textContent='⏳';
      if(!('caches'in window)){off.textContent='❌';setTimeout(function(){off.textContent='📥';},2500);return;}
      caches.open('fishmap-v1').then(function(c){
        return fetch(location.href).then(function(r){if(!r.ok)throw 0;return c.put(location.href,r);});
      }).then(function(){
        off.textContent='✅';off.className='gps-ctrl-btn gps-on';
        setTimeout(function(){off.textContent='📥';off.className='gps-ctrl-btn';},2500);
      }).catch(function(){off.textContent='❌';setTimeout(function(){off.textContent='📥';},2500);});
    });
    /* GPS button */
    var gps=document.createElement('button');
    gps.id='btnGps';gps.className='gps-ctrl-btn';gps.title='Ma position GPS';gps.textContent='📍';
    L.DomEvent.disableClickPropagation(gps);
    gps.addEventListener('click',function(){
      if(_wid!==null){
        navigator.geolocation.clearWatch(_wid);_wid=null;
        if(_mk){map.removeLayer(_mk);_mk=null;}if(_ci){map.removeLayer(_ci);_ci=null;}
        gps.className='gps-ctrl-btn';gps.textContent='📍';
      }else{
        gps.textContent='⏳';
        _wid=navigator.geolocation.watchPosition(function(p){
          gps.className='gps-ctrl-btn gps-on';gps.textContent='📍';
          _placeGps([p.coords.latitude,p.coords.longitude],p.coords.accuracy);
        },function(){
          gps.className='gps-ctrl-btn gps-err';gps.textContent='📍';
          setTimeout(function(){gps.className='gps-ctrl-btn';},3500);_wid=null;
        },{enableHighAccuracy:true,timeout:15000,maximumAge:0});
      }
    });
    wrap.appendChild(off);wrap.appendChild(gps);return wrap;
  };
  ctrl.addTo(map);

  function _placeGps(ll,acc){
    if(!_mk){
      _mk=L.marker(ll,{icon:L.divIcon({className:'',
        html:'<div class="gps-pulse-wrap"><div class="gps-pulse-dot"></div></div>',
        iconSize:[18,18],iconAnchor:[9,9]}),zIndexOffset:1000}).addTo(map);
      _ci=L.circle(ll,{radius:acc,color:'#06b6d4',fillColor:'#06b6d4',fillOpacity:.1,weight:1}).addTo(map);
      map.panTo(ll);
    }else{_mk.setLatLng(ll);_ci.setLatLng(ll).setRadius(acc);}
  }

  /* ── Recenter on lake name click ─────────────────────────────── */
  var h1=document.querySelector('.top-bar h1');
  if(h1){
    h1.title='Recentrer le lac';
    h1.addEventListener('click',function(){
      if(window.geoJsonLayer){try{map.fitBounds(geoJsonLayer.getBounds().pad(.1));return;}catch(e){}}
      if(window.BATHYMETRY_GEOJSON){try{map.fitBounds(L.geoJSON(BATHYMETRY_GEOJSON).getBounds().pad(.1));return;}catch(e){}}
      if(typeof LAT!=='undefined')map.setView([LAT,LON],14);
    });
  }

  /* ── GPS simulation via debug panel ─────────────────────────── */
  var _simActive=false;
  function _addSimRow(){
    var dp=document.getElementById('_debugPanel');
    if(!dp||document.getElementById('_gpsSimChk'))return;
    var row=document.createElement('div');
    row.style.cssText='margin-top:8px;padding-top:6px;border-top:1px solid rgba(148,163,184,.15)';
    row.innerHTML='<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:11px;color:#94a3b8">'
      +'<input type="checkbox" id="_gpsSimChk" style="accent-color:#f59e0b"> 📍 Simuler GPS (clic sur carte)</label>';
    dp.appendChild(row);
    document.getElementById('_gpsSimChk').addEventListener('change',function(){
      _simActive=this.checked;
      map.getContainer().style.cursor=_simActive?'crosshair':'';
      if(!_simActive&&_simMk){map.removeLayer(_simMk);_simMk=null;}
    });
    map.on('click',function(e){
      if(!_simActive)return;
      var ll=[e.latlng.lat,e.latlng.lng];
      if(_simMk)map.removeLayer(_simMk);
      _simMk=L.marker(ll,{icon:L.divIcon({className:'',
        html:'<div style="width:16px;height:16px;background:#f59e0b;border-radius:50%;border:3px dashed #fff;box-shadow:0 0 8px rgba(245,158,11,.6)"></div>',
        iconSize:[16,16],iconAnchor:[8,8]}),zIndexOffset:999,title:'Position simulée'}).addTo(map);
      _placeGps(ll,8);
      var b=document.getElementById('btnGps');if(b)b.className='gps-ctrl-btn gps-on';
    });
  }
  setTimeout(_addSimRow,600);

  /* ── Sun indicator toggle on mobile ─────────────────────────── */
  var sunEl=document.getElementById('sunIndicator');
  if(sunEl&&window.matchMedia('(max-width:768px)').matches){
    var sunBtn=document.createElement('button');
    sunBtn.id='btnSunToggle';sunBtn.title='Simulateur météo';sunBtn.textContent='🌤️';
    sunBtn.style.cssText='position:fixed;bottom:104px;left:8px;z-index:1001;width:36px;height:36px;'
      +'background:rgba(10,14,26,.92);border:1.5px solid rgba(148,163,184,.25);border-radius:8px;'
      +'font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;'
      +'backdrop-filter:blur(6px);box-shadow:0 2px 8px rgba(0,0,0,.45)';
    sunBtn.addEventListener('click',function(){
      var v=sunEl.style.display==='block';
      sunEl.style.display=v?'none':'block';
      sunBtn.style.borderColor=v?'rgba(148,163,184,.25)':'#f59e0b';
    });
    document.body.appendChild(sunBtn);
  }

})();
</script>
<script>
if('serviceWorker'in navigator){navigator.serviceWorker.register('./sw.js',{scope:'./'}).catch(function(){});}
</script>'''

# ── Old block regex ───────────────────────────────────────────────────────────
# Matches everything from "<style>\n/* GPS PWA */" through the SW registration </script>
OLD_BLOCK_RE = re.compile(
    r'<style>\s*\n/\* GPS PWA \*/.*?navigator\.serviceWorker\.register[^\n]*\n[^\n]*\}</script>',
    re.DOTALL
)

# ── Mobile CSS fixes ──────────────────────────────────────────────────────────
OLD_MOBILE_HIDDEN = '  .map-mode-btns { display: none; }\n  .unit-toggle { display: none; }'
NEW_MOBILE_SCROLL = '  .top-bar { overflow-x: auto; -webkit-overflow-scrolling: touch; }\n  .map-mode-btns { display: flex; }\n  .unit-toggle { display: inline-flex; }'


def already_v2(text):
    return 'GPS PWA v2' in text


def patch_file(path: Path) -> str:
    """Patch one HTML file. Returns 'patched', 'already_v2', or 'no_gps_block'."""
    text = path.read_text(encoding='utf-8')

    if already_v2(text):
        return 'already_v2'

    # Replace old GPS block
    new_text, n = OLD_BLOCK_RE.subn(NEW_GPS_BLOCK, text, count=1)
    if n == 0:
        # Old block not found with the exact regex — try broader match
        # Maybe the SW line format is slightly different
        broader = re.compile(
            r'<style>\s*\n/\* GPS PWA \*/.*?</script>\s*\n<script>\s*\nif\(\'serviceWorker\'.*?</script>',
            re.DOTALL
        )
        new_text, n = broader.subn(NEW_GPS_BLOCK, text, count=1)
        if n == 0:
            return 'no_gps_block'

    # Fix mobile CSS: make topbar scrollable, re-enable mode+unit buttons
    new_text = new_text.replace(OLD_MOBILE_HIDDEN, NEW_MOBILE_SCROLL)

    path.write_text(new_text, encoding='utf-8')
    return 'patched'


if __name__ == '__main__':
    targets = sys.argv[1:] if len(sys.argv) > 1 else None

    if targets:
        files = [FISHMAP / t for t in targets]
    else:
        # All HTML files
        files = sorted(FISHMAP.glob('*.html'))

    counts = {'patched': 0, 'already_v2': 0, 'no_gps_block': 0}
    for p in files:
        result = patch_file(p)
        counts[result] += 1
        icon = {'patched': 'OK ', 'already_v2': '-- ', 'no_gps_block': '?? '}[result]
        if result != 'already_v2':
            print(f'  [{icon}] {p.name}  ({result})')

    print(f'\nDone. patched={counts["patched"]}  already_v2={counts["already_v2"]}  no_block={counts["no_gps_block"]}')

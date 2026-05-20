#!/usr/bin/env python3.11
"""
PWA v3 upgrade — patches all HTML files.
Replaces GPS PWA v2 block with v3 that adds:
  - Responsive #_debugPanel: full-width on mobile, scrollable body
  - Auto-collapse simulator panel on mobile (starts closed)
  - 🔧 floating button to toggle simulator on mobile
  - 🌤️ button correctly titled "Soleil & température"
  - #mapWaterTemp hidden on mobile (redundant with sun indicator)
"""
import re, sys
from pathlib import Path

FISHMAP = Path('/Users/michelcousineau/Downloads/fishmap')

NEW_GPS_BLOCK = '''\
<style>
/* GPS PWA v3 */
.gps-ctrl-btn{width:36px;height:36px;background:rgba(10,14,26,.92);border:1.5px solid rgba(148,163,184,.25);border-radius:8px;color:#f1f5f9;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px);box-shadow:0 2px 8px rgba(0,0,0,.45);transition:border-color .2s,color .2s}
.gps-ctrl-btn.gps-on{border-color:#06b6d4;color:#06b6d4}
.gps-ctrl-btn.gps-err{border-color:#ef4444;color:#ef4444}
.gps-pulse-wrap{width:18px;height:18px;position:relative}
.gps-pulse-dot{position:absolute;top:2px;left:2px;width:14px;height:14px;background:#06b6d4;border-radius:50%;border:2px solid #fff;animation:gpsRing 1.6s ease-out infinite}
@keyframes gpsRing{0%{box-shadow:0 0 0 0 rgba(6,182,212,.7)}70%{box-shadow:0 0 0 10px rgba(6,182,212,0)}100%{box-shadow:0 0 0 0 rgba(6,182,212,0)}}
.top-bar h1{cursor:pointer;user-select:none}
.top-bar h1:active{opacity:.75}
@media(max-width:768px){
  #_debugPanel{width:auto!important;left:8px!important;right:8px!important;bottom:80px!important}
  #_debugBody{max-height:50vh;overflow-y:auto}
  #mapWaterTemp{display:none!important}
}
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
    sunBtn.id='btnSunToggle';sunBtn.title='Soleil & température';sunBtn.textContent='🌤️';
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

  /* ── Mobile: auto-collapse sim + 🔧 toggle button ────────────── */
  (function _mobileSetup(){
    if(!window.matchMedia('(max-width:768px)').matches)return;
    setTimeout(function(){
      /* Auto-collapse the debug panel if it started open */
      var body=document.getElementById('_debugBody');
      if(body&&body.style.display!=='none'&&typeof window._dbToggle==='function'){
        window._dbToggle();
      }
      /* Add 🔧 simulator toggle button */
      if(!document.getElementById('btnSimToggle')){
        var sb=document.createElement('button');
        sb.id='btnSimToggle';sb.title='Simulateur météo';sb.textContent='🔧';
        sb.style.cssText='position:fixed;bottom:144px;left:8px;z-index:1001;width:36px;height:36px;'
          +'background:rgba(10,14,26,.92);border:1.5px solid rgba(148,163,184,.25);border-radius:8px;'
          +'font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;'
          +'backdrop-filter:blur(6px);box-shadow:0 2px 8px rgba(0,0,0,.45)';
        sb.addEventListener('click',function(){
          var b=document.getElementById('_debugBody');
          if(!b)return;
          var wasOpen=b.style.display!=='none';
          if(typeof window._dbToggle==='function')window._dbToggle();
          sb.style.borderColor=wasOpen?'rgba(148,163,184,.25)':'#f59e0b';
        });
        document.body.appendChild(sb);
      }
    },750);
  })();

})();
</script>
<script>
if('serviceWorker'in navigator){navigator.serviceWorker.register('./sw.js',{scope:'./'}).catch(function(){});}
</script>'''

OLD_BLOCK_RE = re.compile(
    r'<style>\s*\n/\* GPS PWA v2 \*/.*?navigator\.serviceWorker\.register[^\n]*\n[^\n]*\}</script>',
    re.DOTALL
)


def already_v3(text):
    return 'GPS PWA v3' in text


def patch_file(path: Path) -> str:
    """Patch one HTML file. Returns 'patched', 'already_v3', or 'no_gps_block'."""
    text = path.read_text(encoding='utf-8')

    if already_v3(text):
        return 'already_v3'

    new_text, n = OLD_BLOCK_RE.subn(NEW_GPS_BLOCK, text, count=1)
    if n == 0:
        # Try broader match for slight format variations
        broader = re.compile(
            r'<style>\s*\n/\* GPS PWA v2 \*/.*?</script>\s*\n<script>\s*\nif\(\'serviceWorker\'.*?</script>',
            re.DOTALL
        )
        new_text, n = broader.subn(NEW_GPS_BLOCK, text, count=1)
        if n == 0:
            return 'no_gps_block'

    path.write_text(new_text, encoding='utf-8')
    return 'patched'


if __name__ == '__main__':
    targets = sys.argv[1:] if len(sys.argv) > 1 else None

    if targets:
        files = [FISHMAP / t for t in targets]
    else:
        files = sorted(FISHMAP.glob('*.html'))

    counts = {'patched': 0, 'already_v3': 0, 'no_gps_block': 0}
    for p in files:
        result = patch_file(p)
        counts[result] += 1
        icon = {'patched': 'OK ', 'already_v3': '-- ', 'no_gps_block': '?? '}[result]
        if result != 'already_v3':
            print(f'  [{icon}] {p.name}  ({result})')

    print(f'\nDone. patched={counts["patched"]}  already_v3={counts["already_v3"]}  no_block={counts["no_gps_block"]}')

// FishMap PWA Service Worker — cache-first pour app shell, réseau pour tuiles
const CACHE_V = 'fishmap-v1';

// App shell: tout ce qui peut être préchargé ou mis en cache à la visite
const STATIC_SHELL = [
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
];

// Toutes les cartes de lacs (mise en cache à la visite ou via "Télécharger tout")
const ALL_MAPS = [
  './lac_a_paner_peche.html',
  './lac_anselme_peche.html',
  './lac_au_sable_peche.html',
  './lac_au_tremble_peche.html',
  './lac_aux_lezards_peche.html',
  './lac_baie_peche.html',
  './lac_bigorne_peche.html',
  './lac_bourgeois_peche.html',
  './lac_caillette_peche.html',
  './lac_charme_peche.html',
  './lac_chutenoire_peche.html',
  './lac_clut_peche.html',
  './lac_coleman_peche.html',
  './lac_de_la_baie_peche.html',
  './lac_de_la_chute_noire_peche.html',
  './lac_de_la_gitane_peche.html',
  './lac_de_la_griffe_peche.html',
  './lac_de_la_rencontre_peche.html',
  './lac_des_demoiselles_peche.html',
  './lac_des_joncs_peche.html',
  './lac_des_loups_peche.html',
  './lac_des_mauves_peche.html',
  './lac_des_ronces_peche.html',
  './lac_des_saules_peche.html',
  './lac_diablos_peche.html',
  './lac_dore_peche.html',
  './lac_du_brasier_peche.html',
  './lac_du_chipeau_peche.html',
  './lac_du_grillon_peche.html',
  './lac_du_gros_ours_peche.html',
  './lac_du_hetre_peche.html',
  './lac_du_meta_peche.html',
  './lac_du_rat_musque_peche.html',
  './lac_du_rutabaga_peche.html',
  './lac_du_serpent_peche.html',
  './lac_du_soufflet_peche.html',
  './lac_du_sud_est_peche.html',
  './lac_ephemere_peche.html',
  './lac_forestier_peche.html',
  './lac_fox_peche.html',
  './lac_grand_lac_des_iles_peche.html',
  './lac_green_peche.html',
  './lac_henri_peche.html',
  './lac_jane_peche.html',
  './lac_l_orignal_peche.html',
  './lac_lafond_peche.html',
  './lac_lemay_peche.html',
  './lac_marcel_peche.html',
  './lac_mas_carte_grand_lac_des_iles_peche.html',
  './lac_moyen_peche.html',
  './lac_osborn_peche.html',
  './lac_oudiette_peche.html',
  './lac_peche.html',
  './lac_portage_peche.html',
  './lac_prudent_peche.html',
  './lac_punaise_peche.html',
  './lac_recto_peche.html',
  './lac_regis_peche.html',
  './lac_romeo_peche.html',
  './lac_sable_peche.html',
  './lac_saules_peche.html',
  './lac_siffleux_peche.html',
  './lac_sonois_peche.html',
  './lac_soufflet_peche.html',
  './lac_theodule_peche.html',
  './lac_traverse_peche.html',
  './lac_verdun_peche.html',
  './lac_verso_peche.html',
  './lac_victoire_peche.html',
];

// ── Installation: pré-cache l'app shell ──────────────────────────────────
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_V).then(cache => cache.addAll(STATIC_SHELL))
  );
});

// ── Activation: purge les vieux caches ───────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_V).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── Fetch: cache-first pour HTML/assets, réseau pour tuiles satellite ────
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // Tuiles satellite/OSM: réseau uniquement — trop volumineuses à cacher
  const isTile = url.hostname.includes('tile') ||
                 url.hostname.includes('mapbox') ||
                 url.hostname.includes('arcgis') ||
                 url.pathname.match(/\/(tiles|arcgis)\//);
  if (isTile) return;

  // Pages HTML et assets: cache-first, mise à jour en arrière-plan
  event.respondWith(
    caches.open(CACHE_V).then(async cache => {
      const cached = await cache.match(event.request);
      const networkFetch = fetch(event.request)
        .then(res => { if (res && res.ok) cache.put(event.request, res.clone()); return res; })
        .catch(() => null);
      // Cache-first: retourne le cache immédiatement si disponible, sinon attend le réseau
      return cached || networkFetch;
    })
  );
});

// ── Message: commande "cache-all" depuis l'index ──────────────────────────
self.addEventListener('message', event => {
  if (event.data?.type === 'CACHE_ALL_MAPS') {
    const port = event.ports[0];
    caches.open(CACHE_V).then(async cache => {
      let done = 0;
      for (const url of ALL_MAPS) {
        try {
          const res = await fetch(url);
          if (res.ok) await cache.put(url, res);
        } catch (_) {}
        done++;
        if (port) port.postMessage({ done, total: ALL_MAPS.length, url });
      }
      if (port) port.postMessage({ done: ALL_MAPS.length, total: ALL_MAPS.length, complete: true });
    });
  }
});

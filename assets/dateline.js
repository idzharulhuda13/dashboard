/* dateline.js — satu definisi untuk "Data per <tanggal>" di semua dashboard.
 *
 * Aturan: strip statis di HTML sudah menuliskan tanggal terakhir yang diketahui saat build.
 * Skrip ini hanya memperbaikinya dari payload yang benar-benar tertanam di halaman.
 * Halaman tidak pernah menuliskan "belum diperbarui": pembaca cukup tahu data per tanggal berapa,
 * dan pemantauan kesegaran dilakukan di luar halaman (tools/check_freshness.py).
 *
 * Tanpa JavaScript, strip statis tetap terbaca. Tidak ada fetch: nol permintaan jaringan.
 */
(function () {
  'use strict';

  var MONTHS = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli',
    'Agustus', 'September', 'Oktober', 'November', 'Desember'];

  // Batas umur data sebelum dianggap berhenti, per cadence halaman.
  var THRESHOLD = { daily: 2, weekly: 9, monthly: 40, quarterly: 110, yearly: 400 };

  var DATE_KEY = /(^|_)(as_?of|date|dates|updated|update|release|latest|published|period|through|until)(_|$)/i;

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  function isoFrom(value) {
    if (value == null) return null;
    if (typeof value === 'string') {
      var m = value.match(/(\d{4})-(\d{2})-(\d{2})/);
      if (m) return m[1] + '-' + m[2] + '-' + m[3];
      var d = new Date(value);
      return isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
    }
    if (typeof value === 'object') {
      if (value.d || value.iso || value.date || value.value) {
        return isoFrom(value.d || value.iso || value.date || value.value);
      }
    }
    return null;
  }

  function longDate(iso) {
    var p = iso.split('-');
    return Number(p[2]) + ' ' + MONTHS[Number(p[1]) - 1] + ' ' + p[0];
  }

  // Cari tanggal terbaru di dalam payload, hanya pada kunci yang berarti tanggal.
  function deepLatest(node, depth, acc) {
    if (depth > 6 || node == null || typeof node !== 'object') return acc;
    if (Array.isArray(node)) {
      for (var i = 0; i < node.length && i < 400; i++) deepLatest(node[i], depth + 1, acc);
      return acc;
    }
    for (var k in node) {
      if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
      var v = node[k];
      if (DATE_KEY.test(k)) {
        var iso = isoFrom(v);
        if (iso && (!acc || iso > acc)) acc = iso;
      }
      if (v && typeof v === 'object') acc = deepLatest(v, depth + 1, acc);
    }
    return acc;
  }

  function fromPayload() {
    var data;
    try { data = DATA; } catch (e) { return null; }   // DATA belum ada di halaman ini
    if (!data) return null;
    var candidates = [
      data.meta && data.meta.as_of,
      data.meta && data.meta.as_of_formatted,
      data.as_of,
      data.as_of_formatted,
      data.meta && data.meta.last_date,
      data.last_date,
      data.series && data.series.length ? data.series[data.series.length - 1].date : null,
      data.latest && data.latest.length ? data.latest[0].datetime_utc : null
    ];
    for (var i = 0; i < candidates.length; i++) {
      var iso = isoFrom(candidates[i]);
      if (iso) return iso;
    }
    return deepLatest(data, 0, null);
  }

  function daysBetween(isoA, isoB) {
    var a = Date.parse(isoA + 'T00:00:00Z');
    var b = Date.parse(isoB + 'T00:00:00Z');
    if (isNaN(a) || isNaN(b)) return null;
    return Math.round((b - a) / 86400000);
  }

  function run() {
    var wrap = document.querySelector('[data-cadence][data-dateline]');
    if (!wrap) return;
    var text = wrap.querySelector('[data-dateline-text]');
    if (!text) return;

    var cadence = wrap.getAttribute('data-cadence') || 'daily';
    var limit = THRESHOLD[cadence] || THRESHOLD.daily;
    var staticIso = isoFrom(wrap.getAttribute('data-as-of'));
    var fixed = wrap.hasAttribute('data-fixed');
    var iso = (fixed ? null : fromPayload()) || staticIso;
    if (!iso) return;

    text.textContent = 'Data per ' + longDate(iso);
    wrap.setAttribute('data-resolved', iso);

    // Strip hanya menyebut tanggal. Tidak ada penanda "belum diperbarui" di halaman:
    // kesegaran data cukup dipantau lewat tools/check_freshness.py (cron senyap).
    wrap.setAttribute('data-age-days', String(daysBetween(iso, new Date().toISOString().slice(0, 10))));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();

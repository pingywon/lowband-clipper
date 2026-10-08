/* The editor's link to the real server. See backend-demo.js for the stand-in used on the project page. */
(function () {
'use strict';
var id = new URLSearchParams(location.search).get('tape') || '';
var base = 'api/tape/' + encodeURIComponent(id) + '/';

function json(r) {
  return r.json().catch(function () { throw new Error(r.status === 404 ? 'There is no tape called "' + id + '".' : 'The server answered ' + r.status + '.'); });
}

window.Backend = {
  demo: false,
  videoURL: base + 'video',
  home: { href: './' },
  state: function () { return fetch(base + 'state', { cache: 'no-store' }).then(json); },
  job: function () { return fetch(base + 'job', { cache: 'no-store' }).then(json); },
  post: function (act, body) {
    return fetch(base + act, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(json);
  },
  tapes: function () { return fetch('api/tapes', { cache: 'no-store' }).then(json).then(function (d) { return d.tapes; }); }
};
})();

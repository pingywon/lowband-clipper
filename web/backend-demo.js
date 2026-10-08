/* The editor's stand-in for the server, used by the live demo on the project page.

   There is no server behind a static page, so this keeps the cut list in the browser's own
   storage, applies the same rules through model.js, and pretends to render. Nothing is uploaded
   and no video is cut. */
(function () {
'use strict';
var KEY = 'lowband-clipper.demo.v1', UNDO_MAX = 100;
var M = window.Model, SEED = null, D = null, JOB = { state: 'idle' }, TIMER = null;

function copy(o) { return JSON.parse(JSON.stringify(o)); }
function pad(n, w) { return String(n).padStart(w, '0'); }
function hms(s) { s = Math.max(0, Math.floor(s)); return pad(Math.floor(s / 3600), 2) + '-' + pad(Math.floor(s % 3600 / 60), 2) + '-' + pad(s % 60, 2); }
function clock(s) { return Math.floor(s / 3600) + ':' + pad(Math.floor(s % 3600 / 60), 2) + ':' + pad((s % 60).toFixed(2), 5); }

function save() { try { localStorage.setItem(KEY, JSON.stringify(D)); } catch (e) { /* private window: keep it in memory */ } }
function load() {
  try {
    var d = JSON.parse(localStorage.getItem(KEY));
    if (d && d.seed === SEED.seed && d.cutlist && Array.isArray(d.cutlist.clips) && Array.isArray(d.back) && Array.isArray(d.fwd)) return d;
  } catch (e) { /* nothing stored, or storage is off */ }
  return null;
}

var ready = fetch('state.json', { cache: 'no-cache' }).then(function (r) { return r.json(); }).then(function (seed) {
  SEED = seed;
  D = load() || { seed: seed.seed, cutlist: copy(seed.cutlist), back: [], fwd: [] };
  M.normalise(D.cutlist);
  Backend.videoURL = seed.video;
  Backend.poster = seed.poster;
  return seed;
});

function pretendRender(cl) {
  var clips = copy(cl.clips), i = 0;
  if (TIMER) clearInterval(TIMER);
  JOB = { state: 'running', done: 0, total: clips.length, line: 'starting', error: '' };
  TIMER = setInterval(function () {
    var c = clips[i++];
    JOB.done = i;
    JOB.line = '[' + i + '/' + clips.length + '] ' + pad(c.n, 3) + '_' + hms(c['in']) + '.mp4  ' + clock(c.out - c['in']);
    if (i >= clips.length) {
      clearInterval(TIMER); TIMER = null;
      JOB.state = 'done';
      JOB.note = 'Pretend run finished. The real app would now hold ' + clips.length + ' numbered .mp4 file' + (clips.length === 1 ? '' : 's') + ', cut from the full-quality tape.';
      D.cutlist.submitted.job = 'done'; save();
    }
  }, 650);
}

window.Backend = {
  demo: true,
  videoURL: '',
  home: { href: '../', target: '_top' },
  state: function () {
    return ready.then(function (seed) {
      return { id: seed.id, name: seed.name, version: seed.version, cutlist: copy(D.cutlist), envelope: seed.envelope,
               lines: seed.lines, job: copy(JOB), low: true };
    });
  },
  job: function () { return Promise.resolve(copy(JOB)); },
  post: function (act, body) {
    return ready.then(function () {
      var before = JSON.stringify(D.cutlist), cl = copy(D.cutlist), t;
      if (act === 'undo') {
        var src = body.dir === 'fwd' ? D.fwd : D.back, dst = body.dir === 'fwd' ? D.back : D.fwd;
        if (src.length) { t = src.pop(); dst.push(before); D.cutlist = M.normalise(JSON.parse(t)); save(); }
        if (JOB.state !== 'running') JOB = { state: 'idle' };
        return { cutlist: copy(D.cutlist), job: copy(JOB) };
      }
      if (act === 'reset') {
        cl = copy(SEED.cutlist);
      } else if (act === 'submit') {
        if (!cl.clips.length) return { error: 'nothing marked yet' };
        cl.submitted = { at: new Date().toISOString(), clips: cl.clips.length, total: M.total(cl), job: 'running' };
      } else {
        try { cl = M.apply(cl, act, body || {}); } catch (e) { return { error: e.message }; }
        if (!cl) return { error: 'unknown action' };
      }
      if (act !== 'submit' && JOB.state !== 'running') JOB = { state: 'idle' };   // a finished run describes the old list
      D.back = D.back.concat([before]).slice(-UNDO_MAX);
      D.fwd = [];
      cl.updated = new Date().toISOString();
      D.cutlist = M.normalise(cl);
      save();
      if (act === 'submit') pretendRender(D.cutlist);
      return { cutlist: copy(D.cutlist), job: copy(JOB) };
    });
  }
};
})();

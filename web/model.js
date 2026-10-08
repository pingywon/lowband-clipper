/* The cut-list rules in JavaScript: a mirror of normalise() and apply() in clipkit/model.py.
   The static demo uses it in place of the server. tests/vectors.json runs the same cases
   through both, so the two cannot drift apart. */
(function (root) {
'use strict';
var NOTE_MAX = 200;

function r3(x) { return Math.floor(Number(x) * 1000 + 0.5) / 1000; }

function num(x) {
  var n = Number(x);
  if (x === null || x === undefined || x === '' || !isFinite(n)) throw new Error('not a number');
  return n;
}

function normalise(cl) {
  var dur = cl.duration || 0, frame = 1 / (cl.fps || 24), out = [];
  (cl.clips || []).forEach(function (c) {
    var a = Math.max(0, r3(c['in'])), b = Math.max(0, r3(c.out)), t;
    if (dur > 0) { a = Math.min(a, dur); b = Math.min(b, dur); }
    if (b < a) { t = a; a = b; b = t; }
    if (b - a < frame) return;
    out.push({ n: out.length + 1, 'in': a, out: b, note: (c.note === undefined || c.note === null ? '' : String(c.note)).slice(0, NOTE_MAX) });
  });
  cl.clips = out;
  return cl;
}

function apply(cl, act, body) {
  var clips = cl.clips, n;
  if (act === 'clip') {
    clips.push({ 'in': num(body['in']), out: num(body.out), note: body.note === undefined ? '' : body.note });
  } else if (act === 'del') {
    n = Math.trunc(num(body.n));
    cl.clips = clips.filter(function (c) { return c.n !== n; });
  } else if (act === 'edit') {
    n = Math.trunc(num(body.n));
    clips.forEach(function (c) {
      if (c.n !== n) return;
      if ('in' in body) c['in'] = num(body['in']);
      if ('out' in body) c.out = num(body.out);
      if ('note' in body) c.note = body.note;
    });
  } else if (act === 'order') {
    var by = {}, seen = {}, picked = [];
    clips.forEach(function (c) { by[c.n] = c; });
    (body.order || []).forEach(function (x) {
      var k = Math.trunc(num(x));
      if (by[k] && !seen[k]) { seen[k] = true; picked.push(by[k]); }
    });
    cl.clips = picked.concat(clips.filter(function (c) { return !seen[c.n]; }));
  } else {
    return null;
  }
  return normalise(cl);
}

function total(cl) { return r3(cl.clips.reduce(function (a, c) { return a + c.out - c['in']; }, 0)); }

var Model = { r3: r3, normalise: normalise, apply: apply, total: total };
if (typeof module !== 'undefined' && module.exports) module.exports = Model; else root.Model = Model;
})(typeof window !== 'undefined' ? window : globalThis);

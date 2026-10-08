/* lowband-clipper editor.

   One idea: watch the tape, press the button where a clip starts, press it again where it ends.
   The clip lands in a numbered list. Press SUBMIT and the clips are cut.

   This file never talks to the network itself. Everything goes through window.Backend, so the
   same code runs against the real server (backend-http.js) and in the static demo
   (backend-demo.js). */
(function () {
'use strict';

var B = window.Backend;
var S = null, V = null, OV = null, DET = null;
var CUR = 0;          // playhead, in tape seconds
var WIN = 20;         // the detail strip shows this many seconds either side of the playhead
var IN = null;        // start of the clip being marked, or null
var LN = -1;          // transcript row under the playhead
var STOP = null;      // when previewing one clip: where to stop
var SCRUB = null;     // a timeline drag in progress
var PENDING = null;   // a seek waiting for the previous one to land
var DRAG = null;      // a clip-list reorder in progress
var SWALLOW = false;  // ignore the click that ends a drag
var POLL = null, RAF = 0;

function $(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
function pad(n, w) { return String(n).padStart(w, '0'); }
function tc(s) {
  s = Math.max(0, s || 0);
  var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = Math.floor(s % 60);
  return (h ? h + ':' + pad(m, 2) : m) + ':' + pad(x, 2);
}
function tcf(s) { return tc(s) + '.' + pad(Math.floor((s % 1) * 100), 2); }
function cl() { return S.cutlist.clips; }
function dur() { return S.cutlist.duration || (V && isFinite(V.duration) ? V.duration : 0) || 0; }
function frame() { return 1 / (S.cutlist.fps || 24); }
function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }

function flash(ok) {
  var e = $('saved');
  e.textContent = ok ? ('saved ✓ ' + new Date().toLocaleTimeString()) : 'saving…';
  e.className = ok ? 'on' : '';
}

function post(act, body) {
  flash(false);
  return B.post(act, body || {}).then(function (j) {
    if (j && j.cutlist) { S.cutlist = j.cutlist; flash(true); clips(); draw(); }
    else { $('saved').className = ''; $('saved').textContent = (j && j.error) || 'SAVE FAILED'; }
    if (j && j.job) job(j.job);
    return j;
  }, function () { $('saved').className = ''; $('saved').textContent = 'SAVE FAILED'; });
}

/* ---------- the one interaction ---------- */
function mark() {
  if (IN === null) { IN = CUR; button(); draw(); return; }
  var a = Math.min(IN, CUR), b = Math.max(IN, CUR);
  IN = null; button();
  if (b - a < frame()) { draw(); return; }
  post('clip', { 'in': a, out: b });
}
function cancel() { IN = null; button(); draw(); }

function button() {
  var b = $('mark');
  if (IN === null) {
    b.className = ''; b.textContent = '●  START CLIP HERE';
    $('hint').innerHTML = 'Play the tape. Press <b>the green button</b> (or <b>Enter</b>) where a clip should start, press it again where it should end.';
  } else {
    b.className = 'rec'; b.textContent = '■  END CLIP  —  ' + tc(Math.abs(CUR - IN));
    $('hint').innerHTML = 'Recording from <b>' + tc(IN) + '</b>. Press again to finish, or <b>Esc</b> to throw it away.';
  }
}

/* ---------- clip list ---------- */
function clips() {
  var c = cl(), h = '';
  c.forEach(function (k) {
    h += '<div class="clip" data-n="' + k.n + '" data-t="' + k['in'] + '">' +
      '<b class="grip" title="drag to reorder">⠿</b>' +
      '<i>' + pad(k.n, 3) + '</i>' +
      '<s><u>' + tc(k['in']) + '</u> → ' + tc(k.out) + '  (' + tc(k.out - k['in']) + ')</s>' +
      '<em data-play="' + k.n + '" title="play just this clip">▶</em>' +
      '<em class="x" data-del="' + k.n + '" title="delete">✕</em></div>';
  });
  $('clips').innerHTML = h || '<p class="none">Nothing cut yet.<br><br>Play the tape and press the green button where you want a clip to begin, then again where it ends.</p>';
  var tot = c.reduce(function (a, k) { return a + k.out - k['in']; }, 0);
  $('chead').textContent = c.length ? ('CLIPS — ' + c.length + ', ' + tc(tot) + ' total') : 'CLIPS';
  var sb = $('submit'); sb.disabled = !c.length;
  sb.textContent = c.length ? ('SUBMIT ' + plural(c.length, 'CLIP').toUpperCase()) : 'SUBMIT';
  now();
}

function now() {
  var k = -1;
  cl().forEach(function (c) { if (CUR >= c['in'] && CUR < c.out) k = c.n; });
  document.querySelectorAll('.clip').forEach(function (e) { e.classList.toggle('now', +e.dataset.n === k); });
}

/* drag a row to reorder: mouse anywhere on the row, finger on the grip (so the list still scrolls) */
function dragStart(e) {
  var row = e.target.closest('.clip');
  if (!row || e.target.closest('em')) return;
  var grip = !!e.target.closest('.grip');
  if (e.pointerType === 'mouse' ? e.button !== 0 : !grip) return;
  DRAG = { n: +row.dataset.n, row: row, y0: e.clientY, id: e.pointerId, on: false, to: null };
  if (grip) e.preventDefault();
}
function dragMove(e) {
  if (!DRAG || e.pointerId !== DRAG.id) return;
  if (!DRAG.on) {
    if (Math.abs(e.clientY - DRAG.y0) < 5) return;
    DRAG.on = true; DRAG.row.classList.add('drag'); document.body.classList.add('dragging');
  }
  e.preventDefault();
  var box = $('clips'), r = box.getBoundingClientRect();
  if (e.clientY < r.top + 24) box.scrollTop -= 12; else if (e.clientY > r.bottom - 24) box.scrollTop += 12;
  var rows = [].slice.call(box.querySelectorAll('.clip')), at = rows.length;
  for (var i = 0; i < rows.length; i++) {
    var q = rows[i].getBoundingClientRect();
    if (e.clientY < q.top + q.height / 2) { at = i; break; }
  }
  DRAG.to = at;
  rows.forEach(function (x, i) {
    x.classList.toggle('before', i === at);
    x.classList.toggle('after', at === rows.length && i === rows.length - 1);
  });
}
function dragEnd(e) {
  if (!DRAG || e.pointerId !== DRAG.id) return;
  var d = DRAG; DRAG = null;
  document.body.classList.remove('dragging');
  document.querySelectorAll('.clip').forEach(function (x) { x.classList.remove('drag', 'before', 'after'); });
  if (!d.on) return;
  SWALLOW = true; setTimeout(function () { SWALLOW = false; }, 0);
  if (d.to === null || e.type === 'pointercancel') return;
  var order = cl().map(function (c) { return c.n; }), from = order.indexOf(d.n);
  if (from < 0) return;
  var at = d.to > from ? d.to - 1 : d.to;
  if (at === from) return;
  order.splice(from, 1); order.splice(at, 0, d.n);
  post('order', { order: order });
}

/* ---------- timeline ---------- */
function fit(c) {
  var d = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * d) || c.height !== Math.round(h * d)) { c.width = Math.round(w * d); c.height = Math.round(h * d); }
  var x = c.getContext('2d'); x.setTransform(d, 0, 0, d, 0, 0); x.clearRect(0, 0, w, h);
  return { g: x, w: w, h: h };
}

function win() {
  if (SCRUB && SCRUB.c === DET) return [SCRUB.t0, SCRUB.t1];   // held still while dragging on it
  var D = dur(), t0 = Math.max(0, CUR - WIN), t1 = Math.min(D || CUR + WIN, CUR + WIN);
  if (t1 - t0 < 1) t1 = t0 + 1;
  return [t0, t1];
}

function wave(g, W, y, h, t0, t1) {
  var e = S.envelope || {}, v = e.v || [], hz = e.hz || 20;
  if (!v.length) return;
  g.fillStyle = '#2e3d34';
  for (var px = 0; px < W; px++) {
    var a = t0 + (t1 - t0) * px / W, b = t0 + (t1 - t0) * (px + 1) / W;
    var i = Math.max(0, Math.floor(a * hz)), j = Math.min(v.length, Math.max(i + 1, Math.ceil(b * hz))), m = 0;
    for (var k = i; k < j; k++) { if (v[k] > m) m = v[k]; }
    var hh = Math.max(1, m / 100 * h); g.fillRect(px, y + (h - hh) / 2, 1, hh);
  }
}

function bars(g, W, y, h, t0, t1, lab) {
  cl().forEach(function (c) {
    if (c.out < t0 || c['in'] > t1) return;
    var a = W * (Math.max(c['in'], t0) - t0) / (t1 - t0), b = W * (Math.min(c.out, t1) - t0) / (t1 - t0);
    g.fillStyle = '#2f6b46'; g.fillRect(a, y, Math.max(2, b - a), h);
    g.fillStyle = '#8dffb0'; g.fillRect(a, y, 1, h); g.fillRect(b - 1, y, 1, h);
    if (lab && b - a > 26) { g.fillStyle = '#d8ffe8'; g.font = '10px ui-monospace,Menlo,Consolas,monospace'; g.fillText(pad(c.n, 3), a + 3, y + h - 4); }
  });
  if (IN !== null) {                                            // the clip being marked right now
    var a = W * (Math.max(Math.min(IN, CUR), t0) - t0) / (t1 - t0), b = W * (Math.min(Math.max(IN, CUR), t1) - t0) / (t1 - t0);
    g.fillStyle = 'rgba(255,90,90,.45)'; g.fillRect(a, y, Math.max(2, b - a), h);
    g.fillStyle = '#ff6b6b'; g.fillRect(a, y, 1, h);
  }
}

function ruler(g, W, y, t0, t1) {
  var span = t1 - t0, steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600], st = 3600;
  for (var i = 0; i < steps.length; i++) { if (span / steps[i] <= 9) { st = steps[i]; break; } }
  g.fillStyle = '#5a5a5a'; g.font = '10px ui-monospace,Menlo,Consolas,monospace'; g.strokeStyle = '#2a2a2a'; g.beginPath();
  for (var t = Math.ceil(t0 / st) * st; t <= t1; t += st) {
    var x = Math.round(W * (t - t0) / span) + .5;
    g.moveTo(x, y); g.lineTo(x, y + 4); g.fillText(tc(t), x + 3, y + 9);
  }
  g.stroke();
}

function head(g, x, y, h) { g.strokeStyle = '#ffd400'; g.beginPath(); g.moveTo(Math.round(x) + .5, y); g.lineTo(Math.round(x) + .5, y + h); g.stroke(); }

function draw() {
  if (!S) return;
  var D = dur() || 1, w = win(), t0 = w[0], t1 = w[1];
  var o = fit(OV); bars(o.g, o.w, 0, o.h - 11, 0, D, false); ruler(o.g, o.w, o.h - 11, 0, D);
  head(o.g, o.w * CUR / D, 0, o.h - 11);
  var d = fit(DET); ruler(d.g, d.w, 0, t0, t1); wave(d.g, d.w, 14, 44, t0, t1); bars(d.g, d.w, 60, d.h - 60, t0, t1, true);
  head(d.g, d.w * (CUR - t0) / (t1 - t0), 0, d.h);
  $('clock').textContent = tcf(CUR) + ' / ' + tc(dur());
  if (IN !== null) button();
}

function xt(c, ev, t0, t1) {
  var b = c.getBoundingClientRect();
  return t0 + (t1 - t0) * Math.max(0, Math.min(1, (ev.clientX - b.left) / b.width));
}

/* press and drag along either strip to scrub */
function scrubStart(c, e) {
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  var w = (c === DET) ? win() : [0, dur() || 1];
  SCRUB = { c: c, t0: w[0], t1: w[1], id: e.pointerId, was: !V.paused };
  if (SCRUB.was) V.pause();
  STOP = null;
  try { c.setPointerCapture(e.pointerId); } catch (x) { /* synthetic pointers cannot be captured */ }
  e.preventDefault();
  seek(xt(c, e, SCRUB.t0, SCRUB.t1));
}
function scrubMove(e) { if (SCRUB && e.pointerId === SCRUB.id) seek(xt(SCRUB.c, e, SCRUB.t0, SCRUB.t1)); }
function scrubEnd(e) {
  if (!SCRUB || e.pointerId !== SCRUB.id) return;
  var s = SCRUB; SCRUB = null;
  if (s.was) start();
  draw();
}

/* ---------- playback ---------- */
function goTo(t) {                 // one seek at a time: a newer target replaces a waiting one
  if (V.seeking) { PENDING = t; return; }
  PENDING = null;
  try { V.currentTime = t; } catch (x) { /* not ready yet */ }
}
function seek(t) { CUR = Math.max(0, Math.min(dur() || t, t)); goTo(CUR); draw(); follow(); now(); }
function nudge(d) { STOP = null; seek(CUR + d); }
function start() { var p = V.play(); if (p && p.catch) p.catch(function () { /* paused again before it began */ }); }
function play() { if (V.paused) start(); else V.pause(); }
function rate(r) {
  V.playbackRate = r;
  document.querySelectorAll('[data-rate]').forEach(function (e) { e.classList.toggle('on', +e.dataset.rate === r); });
}
function preview(n) {
  var c = cl().filter(function (k) { return k.n === n; })[0];
  if (!c) return;
  seek(c['in']); STOP = c.out; start();
}
function tick() {                  // while playing, follow the picture every frame
  RAF = 0;
  if (V.paused) return;
  var settled = !V.seeking && PENDING === null;         // a jump still loading says nothing about where we are
  if (settled && STOP !== null && V.currentTime >= STOP) { V.pause(); STOP = null; }
  if (settled && !SCRUB) { CUR = V.currentTime; draw(); follow(); now(); }
  RAF = requestAnimationFrame(tick);
}

/* ---------- transcript ---------- */
function follow() {
  var L = S.lines;
  if (!L.length) return;
  var lo = 0, hi = L.length - 1, f = -1;
  while (lo <= hi) {
    var m = (lo + hi) >> 1;
    if (L[m].s <= CUR && CUR < L[m].e) { f = m; break; } else if (L[m].s > CUR) hi = m - 1; else lo = m + 1;
  }
  if (f < 0) f = Math.max(0, lo - 1);
  if (f === LN) return;
  var p = $('ln' + LN); if (p) p.classList.remove('now');
  LN = f;
  var n = $('ln' + f);
  if (n) {
    n.classList.add('now');
    var s = $('lines');
    if (n.offsetTop < s.scrollTop || n.offsetTop > s.scrollTop + s.clientHeight - 30) s.scrollTop = n.offsetTop - s.clientHeight / 2;
  }
}

function transcript() {
  var q = ($('q').value || '').toLowerCase(), h = '';
  S.lines.forEach(function (l, i) {
    if (q && l.t.toLowerCase().indexOf(q) < 0) return;
    h += '<div class="ln' + (l.r === 'q' ? ' q' : '') + '" id="ln' + i + '" data-t="' + l.s + '"><time>' + tc(l.s) + '</time><span>' + esc(l.t) + '</span></div>';
  });
  $('lines').innerHTML = h || '<p class="none">' + (S.lines.length ? 'No line matches.' :
    'No transcript for this tape. Put a .srt, .vtt or .json file with the same name next to it and reload.') + '</p>';
  LN = -1; follow();
}

/* ---------- the render job ---------- */
function ask() {
  var n = cl().length;
  if (!n) return;
  $('job').className = 'on ask';
  $('jt').textContent = 'Render ' + plural(n, 'clip') + '?';
  $('jl').textContent = B.demo ? 'Pretend run: a web page cannot cut video. The real app cuts them from the full-quality tape.'
                               : 'They are cut from the full-quality tape as numbered files.';
  draw();
}
function job(j) {
  var e = $('job');
  if (!j || j.state === 'idle') { if (!e.classList.contains('ask')) e.className = ''; draw(); return; }
  e.className = 'on ' + (j.state === 'done' ? 'done' : (j.state === 'failed' ? 'failed' : ''));
  if (j.state === 'running') {
    $('jt').textContent = 'Rendering your clips' + (j.total ? ('  ' + j.done + ' / ' + j.total) : '…');
    $('jb').style.width = (j.total ? Math.round(100 * j.done / j.total) : 0) + '%';
    $('jl').textContent = j.line || '';
    if (!POLL) POLL = setInterval(function () { B.job().then(job, function () {}); }, B.demo ? 400 : 1500);
  } else {
    if (POLL) { clearInterval(POLL); POLL = null; }
    $('jb').style.width = '100%';
    if (j.state === 'done') {
      $('jt').textContent = '✓ Done';
      $('jl').textContent = j.note || ('Your clips are in ' + (j.out || 'the clips folder'));
    } else {
      $('jt').textContent = '✕ Render failed';
      $('jl').textContent = j.error || '';
    }
  }
  setTimeout(draw, 60);
}

/* ---------- keys ---------- */
function typing(ev) { return /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName); }
function key(ev) {
  if (typing(ev) || !S) return;
  var k = ev.key;
  if (k === 'Enter' || k === 'i' || k === 'I') { ev.preventDefault(); return mark(); }
  if (k === 'Escape') { ev.preventDefault(); return cancel(); }
  if (k === ' ') { ev.preventDefault(); return play(); }
  if (k === 'ArrowLeft') { ev.preventDefault(); return nudge(ev.shiftKey ? -10 : (ev.altKey ? -5 : -1)); }
  if (k === 'ArrowRight') { ev.preventDefault(); return nudge(ev.shiftKey ? 10 : (ev.altKey ? 5 : 1)); }
  if (k === ',') { ev.preventDefault(); return nudge(-frame()); }
  if (k === '.') { ev.preventDefault(); return nudge(frame()); }
  if ((ev.ctrlKey || ev.metaKey) && (k === 'z' || k === 'Z')) { ev.preventDefault(); return post('undo', { dir: ev.shiftKey ? 'fwd' : 'back' }); }
}

/* ---------- boot ---------- */
function boot(state) {
  S = state; V = $('v'); OV = $('ov'); DET = $('det');
  document.title = S.name + ' · lowband-clipper';
  if (B.poster) V.poster = B.poster;
  V.src = B.videoURL;
  if (B.home) { $('home').href = B.home.href; if (B.home.target) $('home').target = B.home.target; }
  $('lowmsg').textContent = B.demo ? 'Live demo. Your clips stay in this browser.' :
    (S.low ? '' : 'No low copy of this tape yet, so remote viewing will be slow. Make one with: clipper.py low');
  $('again').hidden = !B.demo;
  transcript(); clips(); draw(); button();
  if (S.job) job(S.job);

  V.addEventListener('loadedmetadata', function () { if (!S.cutlist.duration && isFinite(V.duration)) S.cutlist.duration = V.duration; draw(); });
  V.addEventListener('play', function () { $('play').textContent = '❙❙ PAUSE'; if (!RAF) RAF = requestAnimationFrame(tick); });
  V.addEventListener('pause', function () { $('play').textContent = '▶ PLAY'; });
  V.addEventListener('seeked', function () {
    if (PENDING !== null) { var t = PENDING; PENDING = null; V.currentTime = t; return; }
    if (!SCRUB) { CUR = V.currentTime; draw(); follow(); now(); }
  });

  [OV, DET].forEach(function (c) {
    c.addEventListener('pointerdown', function (e) { scrubStart(c, e); });
    c.addEventListener('pointermove', scrubMove);
    c.addEventListener('pointerup', scrubEnd);
    c.addEventListener('pointercancel', scrubEnd);
  });

  $('lines').addEventListener('click', function (e) { var d = e.target.closest('.ln'); if (d) { STOP = null; seek(+d.dataset.t); } });
  $('q').addEventListener('input', transcript);

  $('clips').addEventListener('click', function (e) {
    if (SWALLOW) return;
    if (e.target.dataset.del) return post('del', { n: +e.target.dataset.del });
    if (e.target.dataset.play) return preview(+e.target.dataset.play);
    var d = e.target.closest('.clip'); if (d) { STOP = null; seek(+d.dataset.t); }
  });
  $('clips').addEventListener('pointerdown', dragStart);
  window.addEventListener('pointermove', dragMove, { passive: false });
  window.addEventListener('pointerup', dragEnd);
  window.addEventListener('pointercancel', dragEnd);

  $('mark').onclick = mark;
  $('play').onclick = play;
  $('submit').onclick = ask;
  $('go').onclick = function () { $('job').className = ''; post('submit', {}); };
  $('no').onclick = function () { $('job').className = ''; draw(); };
  $('jx').onclick = function () { $('job').className = ''; draw(); };
  $('again').onclick = function () { IN = null; button(); post('reset', {}); };
  document.querySelectorAll('[data-nudge]').forEach(function (b) {
    b.onclick = function () { var d = b.dataset.nudge; nudge(d === 'f' ? frame() : (d === '-f' ? -frame() : +d)); };
  });
  document.querySelectorAll('[data-rate]').forEach(function (b) { b.onclick = function () { rate(+b.dataset.rate); }; });
  /* keep keyboard focus off buttons, so Space and Enter always mean play and mark */
  document.addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) b.blur(); });
  window.addEventListener('keydown', key);
  window.addEventListener('keyup', function (ev) { if (ev.key === ' ' && !typing(ev)) ev.preventDefault(); });
  window.addEventListener('resize', draw);

  if (B.tapes) B.tapes().then(function (list) {
    var p = $('pick');
    if (!list || list.length < 2) return;
    p.innerHTML = list.map(function (t) { return '<option value="' + esc(t.id) + '"' + (t.id === S.id ? ' selected' : '') + '>' + esc(t.name) + '</option>'; }).join('');
    p.hidden = false;
    p.onchange = function () { location.href = 'editor.html?tape=' + encodeURIComponent(p.value); };
  }, function () {});

  window.__clipper = { state: function () { return S; }, cur: function () { return CUR; }, marking: function () { return IN; }, seek: seek };
}

B.state().then(boot).catch(function (e) {
  document.body.innerHTML = '<p id="empty">Could not load this tape.<br>' + esc(e && e.message || e) + '<br><br><a href="./" style="color:#ffd400">back to the tapes</a></p>';
});
})();

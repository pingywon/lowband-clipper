/* The project page's one moving part: how long a jump along the timeline takes at a given
   connection speed, for an ordinary copy of a tape and for the low copy.

   The two constants are averages measured on a 63-minute tape through a throttled link:
   megabits that had to arrive before a picture appeared after a jump. */
(function () {
'use strict';
var ORDINARY = 12.3, LOW = 1.6;            // megabits per jump
var NEED_ORDINARY = 1.9, NEED_LOW = 0.55;  // megabits per second just to keep playing
var MIN = 0.5, MAX = 25;

function $(id) { return document.getElementById(id); }
function mbps(pos) { return MIN * Math.pow(MAX / MIN, pos / 1000); }  // the slider is logarithmic
function fmt(s) { return s >= 10 ? s.toFixed(0) : s.toFixed(1); }

var speed = $('speed'), timers = [];

function update() {
  var v = mbps(+speed.value), a = ORDINARY / v, b = LOW / v, top = ORDINARY / MIN;
  $('mbps').textContent = v >= 10 ? v.toFixed(0) : v.toFixed(1);
  $('waitA').textContent = fmt(a);
  $('waitB').textContent = fmt(b);
  $('barA').style.width = Math.max(1, 100 * a / top) + '%';
  $('barB').style.width = Math.max(1, 100 * b / top) + '%';
  var playsA = v >= NEED_ORDINARY * 1.25, playsB = v >= NEED_LOW * 1.25;
  $('verdict').innerHTML =
    'Each jump costs <b>' + fmt(a) + ' s</b> of staring at nothing on the ordinary copy, <b>' + fmt(b) + ' s</b> on the low copy. ' +
    (playsA ? 'Both keep playing at this speed.' :
     playsB ? 'And the ordinary copy cannot even keep playing at this speed: it needs ' + NEED_ORDINARY + ' Mbps before any jumping. The low copy plays.' :
              'At this speed even the low copy will stop to catch its breath.');
  return { a: a, b: b };
}

function run(screen, seconds) {
  var bar = screen.querySelector('i');
  screen.classList.remove('shown');
  bar.style.transition = 'none'; bar.style.width = '0';
  void bar.offsetWidth;                                   // restart the animation
  bar.style.transition = 'width ' + seconds + 's linear'; bar.style.width = '100%';
  timers.push(setTimeout(function () { screen.classList.add('shown'); }, seconds * 1000));
}

$('jump').addEventListener('click', function () {
  var w = update();
  timers.forEach(clearTimeout); timers = [];
  run($('scrA'), w.a); run($('scrB'), w.b);
});
speed.addEventListener('input', update);
update();

/* give the embedded editor the keyboard as soon as it is clicked, so Space and Enter go to it */
var demo = $('demo');
if (demo) demo.addEventListener('load', function () {
  try { demo.contentWindow.addEventListener('pointerdown', function () { demo.contentWindow.focus(); }); } catch (e) { /* not same-origin when opened from a file */ }
});
})();

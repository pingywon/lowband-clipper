// node --test                The same cases as tests/test_model.py, run through web/model.js.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const M = require('../web/model.js');
const vectors = JSON.parse(readFileSync(new URL('./vectors.json', import.meta.url), 'utf8'));

for (const v of vectors) {
  test(v.name, () => {
    let cl = M.normalise(JSON.parse(JSON.stringify(v.start)));
    for (const [act, body] of v.steps) cl = M.apply(cl, act, body);
    assert.deepEqual(cl.clips.map(c => [c.n, c.in, c.out, c.note]), v.expect);
  });
}

test('an unknown action is refused', () => {
  assert.equal(M.apply({ duration: 10, fps: 24, clips: [] }, 'explode', {}), null);
});

test('text that is not a number is refused', () => {
  assert.throws(() => M.apply({ duration: 10, fps: 24, clips: [] }, 'clip', { in: 'abc', out: 3 }));
});

test('notes are cut to 200 characters', () => {
  const cl = M.apply({ duration: 10, fps: 24, clips: [] }, 'clip', { in: 1, out: 2, note: 'x'.repeat(500) });
  assert.equal(cl.clips[0].note.length, 200);
});

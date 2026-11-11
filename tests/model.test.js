import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encode, decode, transform, apply, inverse, cut } from '../src/model.js';
import fs from 'node:fs';
import { annotationLayout } from '../src/model.js';
test('annotation wrapping fits long words and paragraphs inside export bounds', () => {
  const measure = (text, size) => Array.from(text).length * size * 0.65;
  const result = annotationLayout(
    'Synthetic annotation: explain each step in your own words.\nΣx² = result',
    280,
    100,
    24,
    measure,
  );
  assert.equal(result.truncated, false);
  assert.ok(result.lines.length * result.lineHeight <= 84);
  assert.ok(result.lines.every((line) => measure(line, result.size) <= 264));
  const word = annotationLayout('supercalifragilisticexpialidocious', 90, 200, 24, measure);
  assert.equal(word.lines.join(''), 'supercalifragilisticexpialidocious');
  assert.ok(word.lines.every((line) => measure(line, word.size) <= 74));
});
test('overflow uses a visible ellipsis without changing the full stored text', () => {
  const original = 'Preserved full annotation '.repeat(100);
  const measure = (text, size) => text.length * size * 0.6;
  const result = annotationLayout(original, 80, 60, 24, measure);
  assert.equal(result.truncated, true);
  assert.ok(result.lines.at(-1).endsWith('…'));
  assert.ok(result.lines.length * result.lineHeight <= 44);
  assert.ok(result.lines.every((line) => measure(line, result.size) <= 64));
  assert.equal(original.length, 2600);
  assert.deepEqual(annotationLayout(original, 8, 8, 24, measure).lines, []);
});
test('pressure and coordinates round trip', () => {
  const points = [
    { x: 125.25, y: 38.5, z: 0.5 },
    { x: 127.25, y: 37.5, z: 0.75 },
    { x: 130.25, y: 44.5, z: 1 },
  ];
  assert.deepEqual(decode({ path: encode(points) }), points);
});
test('nested transformed coordinates round trip', () => {
  let parent = { id: 'p', typeName: 'shape', x: 200, y: 500, rotation: 0.5 },
    s = { id: 's', typeName: 'shape', parentId: 'p', x: 20, y: 10, rotation: 0.2 },
    m = transform(s, { p: parent });
  let p = { x: 50, y: 40 },
    out = inverse(m, apply(m, p));
  assert.ok(Math.abs(out.x - p.x) < 1e-8 && Math.abs(out.y - p.y) < 1e-8);
});
test('partial eraser cuts a segment crossing its circle', () => {
  const runs = cut(
    [
      { x: 0, y: 0, z: 0.2 },
      { x: 100, y: 0, z: 0.8 },
    ],
    { x: 50, y: 0 },
    10,
  );
  assert.equal(runs.length, 2);
  assert.equal(runs[0].at(-1).x, 40);
  assert.equal(runs[1][0].x, 60);
});

import { penSamples, smoothInk } from '../src/model.js';
test('pen samples survive empty, absent and unavailable coalescing', () => {
  for (const method of [
    undefined,
    () => [],
    () => {
      throw Error('unsupported');
    },
  ]) {
    const event = { clientX: 20, clientY: 30, getCoalescedEvents: method };
    assert.deepEqual(penSamples(event), [event]);
  }
  const sample = { clientX: 10 },
    event = { clientX: 20, getCoalescedEvents: () => [sample] };
  assert.deepEqual(penSamples(event), [sample, event]);
});
test('smoothing retains endpoints and rounds corners without overshooting', () => {
  const source = [
      { x: 0, y: 0, z: 0.5 },
      { x: 20, y: 0, z: 0.6 },
      { x: 20, y: 20, z: 0.7 },
    ],
    out = smoothInk(source);
  assert.deepEqual(out[0], source[0]);
  assert.equal(out.at(-1).x, 20);
  assert.equal(out.at(-1).y, 20);
  assert.ok(out.some((p) => p.x > 0 && p.x < 20 && p.y > 0 && p.y < 20));
  assert.ok(
    out.every(
      (p) =>
        p.x >= -1e-9 &&
        p.x <= 20 + 1e-9 &&
        p.y >= -1e-9 &&
        p.y <= 20 + 1e-9 &&
        Number.isFinite(p.z),
    ),
  );
  assert.equal(source.length, 3);
  assert.deepEqual(smoothInk([]), []);
  assert.deepEqual(smoothInk([source[0]]), [source[0]]);
});

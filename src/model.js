// Independent reader/writer for the existing notebook interchange format.
export const clone = (v) => structuredClone(v);
export const palette = {
  black: '#171923',
  grey: '#7c8594',
  red: '#e34f4f',
  'light-red': '#ff9393',
  blue: '#2688dc',
  'light-blue': '#89c9ff',
  green: '#438b45',
  'light-green': '#a3d68b',
  yellow: '#edc84d',
  orange: '#ed963c',
  violet: '#8055bf',
  'light-violet': '#b99cdb',
  white: '#fff',
};
export function half(bits) {
  let e = (bits >> 10) & 31,
    m = bits & 1023;
  return (
    (bits & 32768 ? -1 : 1) *
    (e === 0 ? m * 2 ** -24 : e === 31 ? (m ? NaN : Infinity) : (1 + m / 1024) * 2 ** (e - 15))
  );
}
function packHalf(n) {
  if (!n) return 0;
  const sign = n < 0 ? 32768 : 0,
    a = Math.abs(n),
    e = Math.floor(Math.log2(a));
  if (e < -14) return sign | Math.min(1023, Math.round(a * 2 ** 24));
  let m = Math.round((a / 2 ** e - 1) * 1024),
    ex = e + 15;
  if (m === 1024) {
    m = 0;
    ex++;
  }
  return sign | (Math.min(30, ex) << 10) | m;
}
const decodedCache = new WeakMap();
export function decode(seg) {
  if (decodedCache.has(seg)) return decodedCache.get(seg);
  if (seg.points) return seg.points;
  if (!seg.path) return [];
  const bytes = Uint8Array.from(atob(seg.path), (c) => c.charCodeAt(0)),
    d = new DataView(bytes.buffer),
    dim = seg.dim === 2 ? 2 : 3;
  let p = [d.getFloat32(0, true), d.getFloat32(4, true), dim === 3 ? d.getFloat32(8, true) : 0.5],
    out = [{ x: p[0], y: p[1], z: p[2] }];
  for (let i = dim * 4; i + dim * 2 <= bytes.length; i += dim * 2) {
    for (let k = 0; k < dim; k++) p[k] += half(d.getUint16(i + k * 2, true));
    out.push({ x: p[0], y: p[1], z: p[2] });
  }
  decodedCache.set(seg, out);
  return out;
}
export function encode(points) {
  if (!points.length) return '';
  let bytes = new Uint8Array(12 + (points.length - 1) * 6),
    d = new DataView(bytes.buffer),
    previous = [points[0].x, points[0].y, points[0].z ?? 0.5];
  previous.forEach((n, k) => d.setFloat32(k * 4, n, true));
  for (let i = 1; i < points.length; i++) {
    let v = [points[i].x, points[i].y, points[i].z ?? 0.5];
    for (let k = 0; k < 3; k++)
      d.setUint16(12 + (i - 1) * 6 + k * 2, packHalf(v[k] - previous[k]), true);
    previous = v;
  }
  let str = '';
  for (let b of bytes) str += String.fromCharCode(b);
  return btoa(str);
}
export function plain(r) {
  if (!r) return '';
  if (typeof r === 'string') return r;
  if (r.type === 'text') return r.text || '';
  return (r.content || []).map(plain).join(r.type === 'doc' ? '\n' : '');
}
export const rich = (text) => ({
  type: 'doc',
  content: String(text)
    .split('\n')
    .map((text) => ({ type: 'paragraph', content: text ? [{ type: 'text', text }] : [] })),
});

// The canvas and exports share this layout. Keep full text in the record and
// readable panel; tiny boxes get an ellipsis instead of spilling over nearby ink.
export function annotationLayout(text, width, height, preferredSize, measure) {
  const availableWidth = Math.max(0, width - 16),
    availableHeight = Math.max(0, height - 16);
  const wrap = (size) => {
    const lines = [];
    for (const paragraph of String(text).split('\n')) {
      let line = '';
      for (const word of paragraph.split(/\s+/u)) {
        const candidate = line ? `${line} ${word}` : word;
        if (measure(candidate, size) <= availableWidth) {
          line = candidate;
          continue;
        }
        if (line) lines.push(line);
        line = '';
        for (const character of word) {
          if (line && measure(line + character, size) > availableWidth) {
            lines.push(line);
            line = '';
          }
          line += character;
        }
      }
      lines.push(line);
    }
    return lines;
  };
  for (let size = preferredSize; size >= 12; size--) {
    const lines = wrap(size),
      lineHeight = size * 1.3,
      count = Math.floor(availableHeight / lineHeight);
    if (lines.length <= count) return { lines, size, lineHeight, truncated: false };
    if (size === 12) {
      const visible = lines.slice(0, count);
      if (visible.length) {
        let last = Array.from(visible.at(-1));
        while (last.length && measure(last.join('') + '…', size) > availableWidth) last.pop();
        visible[visible.length - 1] =
          measure('…', size) <= availableWidth ? last.join('') + '…' : '';
      }
      return { lines: visible, size, lineHeight, truncated: true };
    }
  }
}
export function transform(s, store) {
  let chain = [],
    seen = new Set();
  while (s && s.typeName === 'shape' && !seen.has(s.id)) {
    seen.add(s.id);
    chain.unshift(s);
    s = store[s.parentId];
  }
  let m = [1, 0, 0, 1, 0, 0];
  for (const s of chain) {
    const c = Math.cos(s.rotation || 0),
      n = Math.sin(s.rotation || 0),
      [a, b, d, e, x, y] = m;
    m = [
      a * c + d * n,
      b * c + e * n,
      d * c - a * n,
      e * c - b * n,
      x + a * s.x + d * s.y,
      y + b * s.x + e * s.y,
    ];
  }
  return m;
}
export const apply = (m, p) => ({
  x: m[0] * p.x + m[2] * p.y + m[4],
  y: m[1] * p.x + m[3] * p.y + m[5],
  z: p.z,
});
export const inverse = (m, p) => {
  const x = p.x - m[4],
    y = p.y - m[5],
    det = m[0] * m[3] - m[1] * m[2];
  return { x: (m[3] * x - m[2] * y) / det, y: (m[0] * y - m[1] * x) / det, z: p.z };
};
export function strokePoints(s) {
  return (s.props.segments || []).map((seg) =>
    decode(seg).map((p) => ({
      ...p,
      x: p.x * (s.props.scaleX ?? 1),
      y: p.y * (s.props.scaleY ?? 1),
    })),
  );
}
export function bounds(s, store) {
  let p = s.props,
    m = transform(s, store),
    pts;
  if (['draw', 'highlight'].includes(s.type)) pts = strokePoints(s).flat();
  else if (s.type === 'arrow') pts = [p.start, p.end];
  else
    pts = [
      { x: 0, y: 0 },
      { x: p.w || 200, y: 0 },
      { x: 0, y: p.h || 100 },
      { x: p.w || 200, y: p.h || 100 },
    ];
  if (!pts.length) pts = [{ x: 0, y: 0 }];
  pts = pts.map((p) => apply(m, p));
  let x = Math.min(...pts.map((p) => p.x)),
    y = Math.min(...pts.map((p) => p.y));
  return { x, y, w: Math.max(...pts.map((p) => p.x)) - x, h: Math.max(...pts.map((p) => p.y)) - y };
}
export const intersects = (a, b) =>
  a.x <= b.x + b.w && a.x + a.w >= b.x && a.y <= b.y + b.h && a.y + a.h >= b.y;
export function distance(p, a, b) {
  const dx = b.x - a.x,
    dy = b.y - a.y,
    t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
}
export function cut(points, center, r) {
  let runs = [],
    run = [];
  const push = (p) => {
      if (!run.length || Math.hypot(p.x - run.at(-1).x, p.y - run.at(-1).y) > 1e-8) run.push(p);
    },
    end = () => {
      if (run.length) runs.push(run);
      run = [];
    };
  if (points.length === 1)
    return Math.hypot(points[0].x - center.x, points[0].y - center.y) > r ? [points] : [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      dx = b.x - a.x,
      dy = b.y - a.y,
      ox = a.x - center.x,
      oy = a.y - center.y,
      A = dx * dx + dy * dy,
      B = 2 * (ox * dx + oy * dy),
      C = ox * ox + oy * oy - r * r,
      D = B * B - 4 * A * C;
    let ts = [0, 1];
    if (A && D > 0)
      for (const t of [(-B - Math.sqrt(D)) / (2 * A), (-B + Math.sqrt(D)) / (2 * A)])
        if (t > 0 && t < 1) ts.push(t);
    ts.sort((a, b) => a - b);
    const at = (t) => ({
      x: a.x + t * dx,
      y: a.y + t * dy,
      z: (a.z ?? 0.5) + t * ((b.z ?? 0.5) - (a.z ?? 0.5)),
    });
    for (let j = 1; j < ts.length; j++) {
      const mid = at((ts[j - 1] + ts[j]) / 2);
      if (Math.hypot(mid.x - center.x, mid.y - center.y) >= r) {
        push(at(ts[j - 1]));
        push(at(ts[j]));
      } else end();
    }
  }
  end();
  return runs;
}

// Keep the original sample if a browser has no usable coalesced batch.
export function penSamples(event) {
  let samples;
  try {
    samples = event.getCoalescedEvents?.();
  } catch {}
  return samples?.length ? [...samples, event] : [event];
}

// Midpoint quadratics smooth the recorded centerline without inventing corners
// outside its convex hull. Pressure is gently filtered independently of position.
export function smoothInk(points) {
  if (!points.length) return [];
  const filtered = [];
  let pressure = points[0].z ?? 0.5;
  for (const point of points) {
    pressure += ((point.z ?? 0.5) - pressure) * 0.35;
    const p = { x: point.x, y: point.y, z: pressure };
    if (!filtered.length || Math.hypot(p.x - filtered.at(-1).x, p.y - filtered.at(-1).y) > 0.001)
      filtered.push(p);
  }
  if (filtered.length < 3) return filtered;
  const out = [filtered[0]],
    mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 });
  let start = filtered[0];
  for (let i = 1; i < filtered.length; i++) {
    const control = filtered[i],
      end = i === filtered.length - 1 ? control : mid(control, filtered[i + 1]);
    const steps = Math.max(
      1,
      Math.min(
        12,
        Math.ceil(
          (Math.hypot(control.x - start.x, control.y - start.y) +
            Math.hypot(end.x - control.x, end.y - control.y)) /
            2,
        ),
      ),
    );
    for (let j = 1; j <= steps; j++) {
      const t = j / steps,
        u = 1 - t;
      out.push({
        x: u * u * start.x + 2 * u * t * control.x + t * t * end.x,
        y: u * u * start.y + 2 * u * t * control.y + t * t * end.y,
        z: u * u * start.z + 2 * u * t * control.z + t * t * end.z,
      });
    }
    start = end;
  }
  return out;
}

// One filled outline, rather than hundreds of overlapping round line segments.
// This also keeps highlighter opacity uniform along a stroke.
export function inkPath(points, width, isPen) {
  const pts = smoothInk(points),
    path = new Path2D();
  if (!pts.length) return path;
  const radius = (p) => (width * (isPen ? 0.45 + Math.max(0.05, Math.min(1, p.z ?? 0.5)) : 1)) / 2;
  if (pts.length === 1) {
    const p = pts[0];
    path.arc(p.x, p.y, radius(p), 0, Math.PI * 2);
    return path;
  }
  const left = [],
    right = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i],
      a = pts[Math.max(0, i - 1)],
      b = pts[Math.min(pts.length - 1, i + 1)],
      dx = b.x - a.x,
      dy = b.y - a.y,
      length = Math.hypot(dx, dy) || 1,
      r = radius(p);
    left.push({ x: p.x - (dy / length) * r, y: p.y + (dx / length) * r });
    right.push({ x: p.x + (dy / length) * r, y: p.y - (dx / length) * r });
  }
  path.moveTo(right[0].x, right[0].y);
  for (let i = 1; i < right.length; i++) path.lineTo(right[i].x, right[i].y);
  for (let i = left.length - 1; i >= 0; i--) path.lineTo(left[i].x, left[i].y);
  path.closePath();
  for (const p of [pts[0], pts.at(-1)]) {
    path.moveTo(p.x + radius(p), p.y);
    path.arc(p.x, p.y, radius(p), 0, Math.PI * 2);
  }
  return path;
}

// Compute hierarchy keys once per rebuild, rather than on every sort comparison.
// Keep the existing 30-level cap for malformed/cyclic parent chains.
export function sortShapesByDepth(shapes, store) {
  const keys = new Map();
  for (const shape of shapes) {
    let parent = shape, depth = 0;
    while (store[parent.parentId]?.typeName === 'shape' && depth < 30) {
      depth++;
      parent = store[parent.parentId];
    }
    keys.set(shape, { depth, index: String(shape.index) });
  }
  return shapes.sort((a, b) => {
    const left = keys.get(a), right = keys.get(b);
    return left.depth - right.depth ||
      (left.index < right.index ? -1 : left.index > right.index ? 1 : 0);
  });
}

// One pass, without allocating coordinate arrays or spreading a large board
// into Math.min/Math.max (which can exceed the engine's argument limit).
export function drawingBounds(shapes, getBounds) {
  if (!shapes.length) return { x: 0, y: 0, w: 1000, h: 1000 };
  let x = Infinity, y = Infinity, right = -Infinity, bottom = -Infinity;
  for (const shape of shapes) {
    const b = getBounds(shape);
    x = Math.min(x, b.x);
    y = Math.min(y, b.y);
    right = Math.max(right, b.x + b.w);
    bottom = Math.max(bottom, b.y + b.h);
  }
  return { x, y, w: right - x, h: bottom - y };
}

// Bound live-preview work as well as payload size; keep both endpoints.
export function previewPoints(points, limit = 1200) {
  const step = Math.max(1, Math.ceil(points.length / limit));
  const sampled = [];
  for (let i = 0; i < points.length; i += step) sampled.push(points[i]);
  if (points.length && (points.length - 1) % step !== 0) sampled.push(points.at(-1));
  return sampled;
}

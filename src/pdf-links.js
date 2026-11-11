import { transform, inverse } from './model.js';

// PDF annotations are normalized to the rendered page image, not screen pixels.
export function safeWebLink(value) {
  if (typeof value !== 'string' || value.length > 4096) return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export function pageLinkAt(store, frameId, point) {
  if (!store?.[frameId] || store[frameId].meta?.trashed) return null;
  for (const image of Object.values(store)) {
    if (image.type !== 'image' || image.parentId !== frameId || image.meta?.trashed ||
        !Array.isArray(image.meta?.pdfLinks)) continue;
    const p = inverse(transform(image, store), point);
    let x = p.x / image.props.w, y = p.y / image.props.h;
    if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1)) continue;
    if (image.props.flipX) x = 1 - x;
    if (image.props.flipY) y = 1 - y;
    const crop = image.props.crop;
    if (crop) {
      x = crop.topLeft.x + x * (crop.bottomRight.x - crop.topLeft.x);
      y = crop.topLeft.y + y * (crop.bottomRight.y - crop.topLeft.y);
    }
    for (const link of image.meta.pdfLinks.slice(0, 1000)) {
      const r = link?.rect;
      if (!Array.isArray(r) || r.length !== 4 || !r.every(Number.isFinite)) continue;
      if (x < r[0] || y < r[1] || x > r[0] + r[2] || y > r[1] + r[3]) continue;
      const target = store[link.frame];
      if (target?.type === 'frame' && !target.meta?.trashed) return { frame: target.id };
      const url = safeWebLink(link.url);
      if (url) return { url };
    }
  }
  return null;
}

export function isLinkTap(start, end, maxDistance, elapsed) {
  return elapsed >= 0 && elapsed < 700 && maxDistance < 9 &&
    Math.hypot(end.x - start.x, end.y - start.y) < 9;
}

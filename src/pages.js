// Page order and trash are metadata. Moving a page never moves its ink or IDs.
export function belongsTo(record, parent, store) {
  const seen = new Set();
  while (record?.typeName === 'shape' && !seen.has(record.id)) {
    if (record.id === parent) return true;
    seen.add(record.id);
    record = store[record.parentId];
  }
  return false;
}
export function hiddenRecord(record, store) {
  const seen = new Set();
  while (record?.typeName === 'shape' && !seen.has(record.id)) {
    if (record.meta?.trashed) return true;
    seen.add(record.id);
    record = store[record.parentId];
  }
  return false;
}
export function pageAction(store, frames, id, action, value, uid) {
  const frame = store[id];
  if (frame?.type !== 'frame') throw Error('Page no longer exists');
  if (action === 'rename') {
    if (!value?.trim() || value.length > 80) throw Error('Use a page name of 1–80 characters');
    frame.props.name = value.trim();
  } else if (action === 'trash' || action === 'restore') {
    frame.meta = { ...frame.meta, trashed: action === 'trash' };
  } else if (action === 'up' || action === 'down') {
    const order = [...frames],
      from = order.findIndex((f) => f.id === id),
      to = from + (action === 'up' ? -1 : 1);
    if (to < 0 || to >= order.length) return id;
    [order[from], order[to]] = [order[to], order[from]];
    order.forEach((f, i) => (f.meta = { ...f.meta, order: i }));
  } else if (action === 'duplicate') {
    const members = Object.values(store).filter(
      (r) => r.typeName === 'shape' && belongsTo(r, id, store),
    );
    const ids = new Map(members.map((r) => [r.id, uid()]));
    for (const member of members) {
      const copy = structuredClone(member);
      copy.id = ids.get(member.id);
      copy.parentId = ids.get(member.parentId) || member.parentId;
      copy.meta = { ...copy.meta };
      delete copy.meta.operation_id;
      if (copy.meta.pdfLinks) copy.meta.pdfLinks = copy.meta.pdfLinks.map(link =>
        link.frame && ids.has(link.frame) ? {...link, frame:ids.get(link.frame)} : link);
      if (member.id === id) {
        copy.props.name += ' (copy)';
        copy.y = Math.max(...frames.map((f) => f.y + f.props.h)) + 80;
        copy.meta.order = frames.length;
      }
      store[copy.id] = copy;
    }
    return ids.get(id);
  } else throw Error('Unsupported page action');
  return id;
}

// One pass through records, instead of rescanning the whole notebook per page.
export function replacementFrames(frames, store) {
  const counts = new Map();
  for (const record of Object.values(store)) {
    if (record.type === 'image' && record.isLocked)
      counts.set(record.parentId, (counts.get(record.parentId) || 0) + 1);
  }
  return frames.filter(frame => counts.get(frame.id) === 1);
}

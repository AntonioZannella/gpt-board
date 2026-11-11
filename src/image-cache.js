// Bound decoded image memory and retry transient failures without a reload.
export function createImageCache({create = () => new Image(), changed, failed,
                                  later = setTimeout, limit = 12}) {
  const entries = new Map();
  function load(entry) {
    const image = entry.image = create();
    image.onload = () => { entry.failures = 0; changed(); };
    image.onerror = () => {
      failed();
      // A persistent error must not generate an endless request loop.
      if (++entry.failures <= 3) later(() => {
        if (entries.get(entry.id) !== entry) return;
        load(entry);
      }, 1000 * 2 ** (entry.failures - 1));
    };
    image.src = entry.src;
  }
  return {
    get(id, src) {
      if (!src) return null;
      let entry = entries.get(id);
      if (!entry || entry.src !== src) {
        entry = {id, src, failures: 0};
        entries.set(id, entry);
        load(entry);
      }
      entries.delete(id);
      entries.set(id, entry);
      while (entries.size > limit) entries.delete(entries.keys().next().value);
      return entry.image;
    },
  };
}

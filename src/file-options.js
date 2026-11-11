// Page ranges are human-facing and one-based; preserve explicit selection order.
export function pageNumbers(text, count) {
  if (!text.trim() || text.trim().toLowerCase() === 'all') return Array.from({length:count}, (_,i)=>i);
  const result = new Set();
  for (const part of text.split(',')) {
    const match = /^\s*(\d+)\s*(?:-\s*(\d+))?\s*$/.exec(part);
    if (!match) throw Error('Use page numbers such as 1-5, 8, 12-14, or all.');
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end > count) throw Error(`Choose pages between 1 and ${count}.`);
    for (let i=start; i<=end; i++) result.add(i-1);
  }
  return [...result];
}

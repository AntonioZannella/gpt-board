// Opt-in, bounded, browser-local tracing. No notebook content or network writes.
export function installSwipeDiagnostics(canvas, readState) {
  const rows = [], events = new WeakMap();
  let moves = 0, lastMove = 0, received = 0, handled = 0, turns = 0;
  const started = performance.now();
  const output = document.createElement('pre');
  output.id = 'swipe-trace-data';
  output.hidden = true;
  const badge = document.createElement('div');
  badge.id = 'swipe-trace-status';
  badge.style.cssText = 'position:fixed;bottom:36px;left:12px;z-index:50;pointer-events:none;background:#17283e;color:white;padding:8px;font:12px monospace;white-space:pre';
  document.body.append(output, badge);
  const move = () => { moves++; lastMove = performance.now(); };
  const wheel = e => {
    const now = performance.now();
    const row = {
      t: Math.round(now - started), x: e.deltaX, y: e.deltaY, units: e.deltaMode,
      target: e.target?.id || e.target?.tagName || 'unknown',
      workspace: canvas.parentElement.contains(e.target), trusted: e.isTrusted,
      cancelable: e.cancelable, modifiers: [e.ctrlKey, e.metaKey, e.shiftKey],
      moves, sinceMove: lastMove ? Math.round(now - lastMove) : null,
      before: readState(), result: 'not-handled',
    };
    received++;
    rows.push(row);
    if (rows.length > 2000) rows.shift();
    events.set(e, row);
    setTimeout(() => {
      row.prevented = e.defaultPrevented;
      row.after = readState();
    });
  };
  window.addEventListener('pointermove', move, {capture:true, passive:true});
  window.addEventListener('wheel', wheel, {capture:true, passive:true});
  let lastTick = started, maxTickDelay = 0;
  const render = () => {
    const now = performance.now();
    maxTickDelay = Math.max(maxTickDelay, now-lastTick-500);
    lastTick = now;
    const data = {version:'swipe-trace-2', received, handled, turns,
      elapsed:Math.round(now-started), maxTickDelay:Math.round(maxTickDelay), rows};
    output.textContent = JSON.stringify(data);
    badge.textContent = `Swipe diagnostics • received ${received} • handled ${handled} • turns ${turns}\n${rows.at(-1)?.result || 'Ready — swipe over the page'} • local recording (5 minutes)`;
  };
  render();
  const timer = setInterval(render, 500);
  setTimeout(() => {
    clearInterval(timer);
    window.removeEventListener('pointermove', move, true);
    window.removeEventListener('wheel', wheel, true);
    render();
    badge.textContent += '\nRecording finished';
  }, 300000);
  return (event, result, action) => {
    const row = events.get(event);
    if (!row) return;
    handled++;
    if (action?.turn) turns++;
    row.result = result;
    row.action = action;
  };
}

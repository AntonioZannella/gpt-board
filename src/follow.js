// Explicit page links win; an ordinary Mac launch starts at the connected iPad.
export function tabletLaunch(view, params) {
  if (!view || !view.frame || !['notebook', 'whiteboard'].includes(view.kind)
      || typeof view.notebook !== 'string' || params.has('page') || params.get('open') === '1') return null;
  if (params.has('notebook') && params.get('notebook') !== view.notebook) return null;
  const section = view.kind === 'whiteboard' ? 'whiteboards' : 'notebooks';
  if (params.has('section') && params.get('section') !== section) return null;
  return {notebook: view.notebook, section, page: view.frame};
}
export async function resumeTabletPage(params) {
  if (navigator.maxTouchPoints > 1 || params.has('page') || params.get('open') === '1') return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 600);
  try {
    const response = await fetch('/api/events?presence=1', {signal: controller.signal});
    if (!response.ok) return;
    const target = tabletLaunch((await response.json()).view, params);
    if (target) for (const [key, value] of Object.entries(target)) params.set(key, value);
  } catch { /* Offline or no tablet: keep the normal saved location. */ }
  finally { clearTimeout(timeout); }
}
// Page presence is separate from durable handwriting saves.
export function followAction(view, local) {
  if (!view || local.busy) return null;
  if (view.notebook !== local.book || view.kind !== local.kind)
    return { notebook: view.notebook, white: view.kind === 'whiteboard' };
  if (view.frame && view.frame !== local.frame && local.frames.includes(view.frame))
    return { frame: view.frame };
  return null;
}
export function installFollow({tab, book, kind, getState, showPage, navigate, buttonAfter}) {
  const tablet = navigator.maxTouchPoints > 1;
  const key = 'gpt-board:follow-ipad';
  let enabled = false, publishing = false, lastFrame, polling = false, switching = false;
  try { enabled = !tablet && sessionStorage.getItem(key) === '1'; } catch {}
  const button = document.createElement('button');
  button.id = 'follow-ipad';
  button.hidden = tablet;
  buttonAfter.after(button);
  function label(waiting=false) {
    button.setAttribute('aria-pressed', String(enabled));
    button.textContent = enabled ? (waiting ? 'Following · waiting for iPad' : 'Following iPad · Stop') : 'Follow iPad';
    button.title = 'Follow the current page on your iPad. Click again to stop.';
  }
  async function accept(view) {
    if (!enabled || document.hidden || switching) return;
    label(!view);
    const action = followAction(view, {...getState(), book, kind});
    if (action?.notebook) {
      switching = true;
      try { await navigate(action.notebook, action.white); } finally { switching = false; }
    } else if (action?.frame) showPage(action.frame, false);
  }
  async function poll() {
    if (!enabled || document.hidden || polling) return;
    polling = true;
    try {
      const r = await fetch('/api/events?presence=1');
      if (!r.ok) throw Error('Presence unavailable');
      await accept((await r.json()).view);
    } catch { label(true); } finally { polling = false; }
  }
  async function publish(force=false) {
    if (!tablet || publishing) return;
    const frame = document.hidden ? null : getState().frame || null;
    if (!force && frame === lastFrame) return;
    publishing = true;
    try {
      const r = await fetch('/api/events', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({tab, frame, kind, tablet:true, visible:!document.hidden})});
      if (r.ok) {
        lastFrame = frame;
        const latest = document.hidden ? null : getState().frame || null;
        if (latest !== frame) setTimeout(() => publish(), 0);
      }
    } catch {} finally { publishing = false; }
  }
  button.onclick = () => {
    enabled = !enabled;
    try { sessionStorage.setItem(key, enabled ? '1' : '0'); } catch {}
    label(enabled);
    if (enabled) poll();
  };
  label(enabled);
  const pollTimer = setInterval(poll, 2000);
  const heartbeat = setInterval(() => publish(true), 10000);
  document.addEventListener('visibilitychange', () => {publish(true);poll();});
  window.addEventListener('pagehide', () => {clearInterval(pollTimer);clearInterval(heartbeat);});
  return {publish, poll, receive: e => accept(JSON.parse(e.data))};
}

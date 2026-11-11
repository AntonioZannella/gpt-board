// Explicit page links win; an ordinary Mac launch starts at the connected iPad.
export function tabletLaunch(view, params) {
  if (!view || (view.kind === 'notebook' && !view.frame) || !['notebook', 'whiteboard'].includes(view.kind)
      || typeof view.notebook !== 'string' || params.has('page') || params.get('open') === '1') return null;
  if (params.has('notebook') && params.get('notebook') !== view.notebook) return null;
  const section = view.kind === 'whiteboard' ? 'whiteboards' : 'notebooks';
  if (params.has('section') && params.get('section') !== section) return null;
  return {notebook: view.notebook, section, ...(view.frame ? {page: view.frame} : {})};
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
export function validViewport(view) {
  return view && ['x','y','w','h'].every(key => Number.isFinite(view[key]))
    && view.w > 0 && view.h > 0;
}
export function viewportCamera(view, width, height) {
  if (!validViewport(view) || width <= 0 || height <= 0) return null;
  const z = Math.max(0.1, Math.min(8, width / view.w, height / view.h));
  return {x: width / (2*z) - view.x - view.w/2,
    y: height / (2*z) - view.y - view.h/2, z};
}
// Page presence is separate from durable handwriting saves.
export function followAction(view, local) {
  if (!view || local.busy) return null;
  if (view.notebook !== local.book || view.kind !== local.kind)
    return { notebook: view.notebook, white: view.kind === 'whiteboard' };
  if (view.kind === 'whiteboard' && validViewport(view.viewport))
    return {viewport: view.viewport};
  if (view.frame && view.frame !== local.frame && local.frames.includes(view.frame))
    return { frame: view.frame };
  return null;
}
export function installFollow({tab, book, kind, getState, showPage, showViewport, navigate, buttonAfter}) {
  const tablet = navigator.maxTouchPoints > 1;
  const key = 'gpt-board:follow-ipad';
  let enabled = false, publishing = false, lastView, publishTimer, lastPublished = 0, polling = false, switching = false;
  try { enabled = !tablet && sessionStorage.getItem(key) === '1'; } catch {}
  const button = document.createElement('button');
  button.id = 'follow-ipad';
  button.hidden = tablet;
  buttonAfter.after(button);
  function label(waiting=false) {
    button.setAttribute('aria-pressed', String(enabled));
    button.textContent = enabled ? (waiting ? 'Following · waiting for iPad' : 'Following iPad · Stop') : 'Follow iPad';
    button.title = 'Follow the current page or whiteboard view on your iPad. Click again to stop.';
  }
  async function accept(view) {
    if (!enabled || document.hidden || switching) return;
    label(!view);
    const action = followAction(view, {...getState(), book, kind});
    if (action?.notebook) {
      switching = true;
      try { await navigate(action.notebook, action.white); } finally { switching = false; }
    } else if (action?.viewport) showViewport(action.viewport);
    else if (action?.frame) showPage(action.frame, false);
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
    const state = getState();
    const frame = document.hidden ? null : state.frame || null;
    const viewport = kind === 'whiteboard' && !document.hidden ? state.viewport : null;
    const signature = JSON.stringify({frame, viewport});
    if (!force && signature === lastView) return;
    // Send the latest camera at most ten times a second while panning.
    if (!force && Date.now() - lastPublished < 100) {
      clearTimeout(publishTimer);
      publishTimer = setTimeout(() => publish(), 100 - (Date.now() - lastPublished));
      return;
    }
    publishing = true;
    lastPublished = Date.now();
    try {
      const r = await fetch('/api/events', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({tab, frame, viewport, kind, tablet:true, visible:!document.hidden})});
      if (r.ok) {
        lastView = signature;
        publishTimer = setTimeout(() => publish(), 100);
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
  window.addEventListener('pagehide', () => {clearInterval(pollTimer);clearInterval(heartbeat);clearTimeout(publishTimer);});
  return {publish, poll, receive: e => accept(JSON.parse(e.data))};
}

// Native color popovers are avoided: every swatch is a normal Pencil/touch button.
export const inkColors = [
  ['Black', '#171923'], ['Gray', '#64748b'], ['White', '#ffffff'], ['Red', '#dc2626'],
  ['Orange', '#ea580c'], ['Yellow', '#eab308'], ['Green', '#15803d'], ['Teal', '#0d9488'],
  ['Blue', '#2563eb'], ['Navy', '#1e3a8a'], ['Purple', '#7c3aed'], ['Pink', '#db2777'],
];
export function normalizeInk(value) {
  const hex = value.trim().replace(/^#/, '');
  return /^[0-9a-f]{6}$/i.test(hex) ? '#' + hex.toLowerCase() : null;
}
export function bindInkTap(button, action, now = () => performance.now()) {
  let suppressClickUntil = 0;
  button.addEventListener('pointerup', event => {
    if (event.pointerType !== 'pen' || event.button !== 0) return;
    event.preventDefault();
    suppressClickUntil = now() + 500;
    action();
  });
  button.addEventListener('click', event => {
    // Some WebKit versions also synthesize a click after the Pencil event.
    if (now() < suppressClickUntil) { event.preventDefault(); return; }
    action();
  });
}
export function installInkPicker() {
  const get = id => document.getElementById(id);
  const value = get('color'), dialog = get('ink-dialog'), trigger = get('ink-picker');
  const custom = get('ink-custom'), error = get('ink-error'), swatches = [];
  // Dialog backdrop events target the dialog itself; padding inside stays open.
  let outsidePress = false;
  function outside(event) {
    const rect = dialog.getBoundingClientRect();
    return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right ||
      event.clientY < rect.top || event.clientY > rect.bottom);
  }
  dialog.addEventListener('pointerdown', event => { outsidePress = outside(event); });
  dialog.addEventListener('pointerup', event => {
    if (outsidePress && outside(event)) {
      event.preventDefault();
      dialog.close();
    }
    outsidePress = false;
  });
  dialog.addEventListener('pointercancel', () => { outsidePress = false; });
  dialog.addEventListener('close', () => { outsidePress = false; });

  function update() {
    get('ink-swatch').style.backgroundColor = value.value;
    trigger.setAttribute('aria-label', 'Ink color ' + value.value);
    custom.value = value.value;
    for (const [button, color] of swatches) button.setAttribute('aria-pressed', String(color === value.value));
  }
  function choose(color) {
    value.value = color;
    value.dispatchEvent(new Event('change', {bubbles:true}));
    update();
    dialog.close();
  }
  for (const [name, color] of inkColors) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = name;
    const dot = document.createElement('span');
    dot.style.backgroundColor = color; dot.setAttribute('aria-hidden', 'true');
    button.prepend(dot);
    bindInkTap(button, () => choose(color));
    get('ink-colors').append(button); swatches.push([button, color]);
  }
  bindInkTap(trigger, () => {
    update(); error.textContent = '';
    if (!dialog.open) dialog.showModal();
    swatches.find(([,color]) => color === value.value)?.[0].focus();
  });
  bindInkTap(get('ink-done'), () => dialog.close());
  function applyCustom() {
    const color = normalizeInk(custom.value);
    if (color) choose(color);
    else error.textContent = 'Enter six hex digits, such as #2563eb.';
  }
  bindInkTap(get('ink-apply'), applyCustom);
  get('ink-custom-form').addEventListener('submit', event => {
    event.preventDefault(); applyCustom();
  });
  update();
}

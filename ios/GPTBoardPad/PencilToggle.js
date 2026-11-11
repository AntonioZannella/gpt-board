(() => {
  const selected = document.querySelector('[data-tool][aria-pressed="true"]');
  const next = selected?.dataset.tool === 'eraser' ? 'pen' : 'eraser';
  const button = document.querySelector(`[data-tool="${next}"]`);
  if (!selected || !button || button.disabled) return null;
  button.click();
  return next;
})()

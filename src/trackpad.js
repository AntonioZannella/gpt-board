// One page turn per wheel gesture, including the trackpad's momentum tail.
// This stays browser-local: no network requests in the input path.
export function wheelPixels(event, pageHeight = 800) {
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? pageHeight : 1;
  const x = event.deltaX * unit, y = event.deltaY * unit;
  return event.shiftKey && !x ? { x: y, y: 0 } : { x, y };
}

export class TrackpadPager {
  constructor() { this.reset(); }
  reset() {
    this.lastEvent = -Infinity;
    this.x = this.y = 0;
    this.mode = null;
    this.peak = this.previousSpeed = this.tailSamples = 0;
    this.turnedAt = -Infinity;
    this.tailFloor = Infinity;
    this.risingSamples = 0;
    this.sawTail = false;
  }
  update(x, y, time, canTurn) {
    const speed = Math.abs(x), horizontal = speed > Math.abs(y) * 1.5;
    const pause = time - this.lastEvent > 110;
    // Browser wheel events do not expose macOS gesture phases. A quiet gap,
    // deliberate reversal, or renewed acceleration after a decaying tail
    // starts a fresh gesture. A continuing/decaying tail stays consumed.
    const reverse = this.mode === 'turned' && horizontal && speed >= 8 &&
      Math.sign(x) !== Math.sign(this.x);
    if (this.mode === 'turned') {
      // The physical swipe can keep accelerating after the page has turned.
      this.peak = Math.max(this.peak, speed);
      this.tailFloor = Math.min(this.tailFloor, speed);
      this.tailSamples = speed <= this.peak * 0.4 ? this.tailSamples + 1 : 0;
      // A new finger contact can interrupt momentum with just one deep low
      // sample. Requiring three low samples swallows the entire next swipe.
      // Still require a subsequent substantial acceleration and 100ms age.
      this.sawTail ||= this.tailSamples >= 3 ||
        speed <= this.peak * 0.2 && time - this.turnedAt >= 100;
      this.risingSamples = speed > this.previousSpeed + 0.4 ? this.risingSamples + 1 :
        speed >= this.previousSpeed * 0.9 ? this.risingSamples : 0;
    }
    // A fresh push often ramps up over several samples instead of jumping
    // 2.5x in one sample. Compare against the tail's low point as well.
    const freshPush = this.mode === 'turned' && horizontal && this.sawTail &&
      time - this.turnedAt >= 100 && (
        speed >= Math.max(8, this.previousSpeed * 2.5) ||
        this.risingSamples >= 2 && speed >= Math.max(8, this.tailFloor * 3)
      );
    if (pause || reverse || freshPush) this.reset();
    this.lastEvent = time;
    if (this.mode === 'turned') {
      this.previousSpeed = speed;
      return { consume: true };
    }
    this.peak = Math.max(this.peak, speed);
    this.previousSpeed = speed;
    this.x += x; this.y += y;
    if (!this.mode && Math.max(Math.abs(this.x), Math.abs(this.y)) >= 12) {
      this.mode = Math.abs(this.x) > Math.abs(this.y) * 1.5 && canTurn(Math.sign(this.x))
        ? 'page' : 'pan';
    }
    if (this.mode !== 'page') return { consume: false };
    if (Math.abs(this.x) >= 55) {
      this.mode = 'turned';
      this.turnedAt = time;
      return { consume: true, turn: Math.sign(this.x) };
    }
    return { consume: true };
  }
}

export function navigationKey(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey && event.key !== '+') return null;
  if (['ArrowRight', 'PageDown'].includes(event.key)) return event.repeat ? null : 'next';
  if (['ArrowLeft', 'PageUp'].includes(event.key)) return event.repeat ? null : 'previous';
  if (event.key === '+' || event.key === '=') return 'zoom-in';
  if (event.key === '-' || event.key === '_') return 'zoom-out';
  if (event.key === '0') return 'fit';
  return null;
}

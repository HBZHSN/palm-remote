// Pointer Events keep touch and mouse development behavior on the same path.
export class Touchpad {
  constructor(element, send, options = {}) {
    this.el = element; this.send = send; this.options = options;
    this.points = new Map(); this.locked = false; this.dragging = false;
    this.pending = null; this.frame = null;
    element.addEventListener('pointerdown', e => this.down(e));
    element.addEventListener('pointermove', e => this.move(e));
    element.addEventListener('pointerup', e => this.up(e));
    element.addEventListener('pointercancel', () => this.reset());
    element.addEventListener('lostpointercapture', e => { if (this.points.has(e.pointerId)) this.reset(); });
    element.addEventListener('contextmenu', e => e.preventDefault());
  }
  label(text) { this.options.onGesture?.(text); }
  flush() {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    if (this.pending) {
      const {type, dx, dy} = this.pending;
      this.send(type === 'position' ? this.pending : {type, dx: Math.round(dx), dy: Math.round(dy)});
      this.pending = null;
    }
  }
  queue(type, dx, dy) {
    if (this.pending && this.pending.type !== type) this.flush();
    this.pending ||= {type, dx: 0, dy: 0};
    this.pending.dx += dx; this.pending.dy += dy;
    if (this.frame === null) this.frame = requestAnimationFrame(() => this.flush());
  }
  down(e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (this.options.enabled?.() === false) return;
    if (!this.points.size && this.options.direct?.() && !this.position(e)) return;
    e.preventDefault(); this.el.setPointerCapture(e.pointerId);
    if (!this.points.size) {
      this.started = performance.now(); this.travel = 0; this.maxPoints = 0; this.suppress = false;
    }
    this.points.set(e.pointerId, {x: e.clientX, y: e.clientY});
    this.maxPoints = Math.max(this.maxPoints, this.points.size);
    clearTimeout(this.hold);
    if (this.points.size === 1 && !this.locked) {
      this.hold = setTimeout(() => {
        if (this.points.size === 1 && this.travel < 7 && !this.suppress) {
          this.flush(); this.dragging = true;
          this.send({type: 'button', button: 'left', down: true});
          this.el.classList.add('dragging'); this.label('正在拖动');
          this.options.vibrate?.();
        }
      }, 420);
    } else if (this.points.size >= 2) {
      this.flush();
      if (this.dragging || this.locked) { this.send({type: 'release'}); this.setLocked(false); this.dragging = false; }
      this.el.classList.remove('dragging'); this.label('双指滚动');
    }
    if (this.points.size > 2) this.suppress = true;
  }
  move(e) {
    const old = this.points.get(e.pointerId); if (!old) return;
    e.preventDefault();
    const dx = e.clientX - old.x, dy = e.clientY - old.y;
    this.points.set(e.pointerId, {x: e.clientX, y: e.clientY});
    this.travel += Math.hypot(dx, dy);
    if (this.travel > 7) clearTimeout(this.hold);
    if (this.suppress || this.points.size > 2) return;
    if (this.points.size === 2) {
      const direction = this.options.natural?.() === false ? -1 : 1;
      this.queue('scroll', -dx * 1.5 * direction, dy * 1.5 * direction);
    } else if (this.maxPoints === 1) {
      if (this.options.direct?.()) { this.position(e, true); return; }
      const sensitivity = this.options.sensitivity?.() || 1.5;
      const point = this.options.relativePosition?.(dx * sensitivity, dy * sensitivity);
      if (point) { this.queuePosition(point); return; }
      this.queue('move', dx * sensitivity, dy * sensitivity);
      if (!this.dragging && !this.locked) this.label('正在移动');
    }
  }
  position(e, clamp = false) {
    const point = this.options.position?.(e, clamp);
    if (!point) return false;
    if (clamp) {
      this.queuePosition(point);
    } else {
      this.flush(); this.send({type: 'position', ...point});
    }
    return true;
  }
  queuePosition(point) {
    if (this.pending && this.pending.type !== 'position') this.flush();
    this.pending = {type: 'position', ...point};
    if (this.frame === null) this.frame = requestAnimationFrame(() => this.flush());
  }
  up(e) {
    if (!this.points.has(e.pointerId)) return;
    clearTimeout(this.hold); this.flush(); this.points.delete(e.pointerId);
    if (this.points.size) return;
    if (this.dragging) {
      this.send({type: 'button', button: 'left', down: false}); this.dragging = false;
    } else if (!this.suppress && !this.locked && this.travel < 12 && performance.now() - this.started < 320) {
      this.send({type: 'click', button: this.maxPoints === 2 ? 'right' : 'left'});
      this.options.vibrate?.();
    }
    this.el.classList.toggle('dragging', this.locked);
    this.label(this.locked ? '拖动已锁定' : '自在滑动');
  }
  setLocked(value) { this.locked = value; this.options.onLock?.(value); }
  toggleLock() {
    this.flush(); this.setLocked(!this.locked);
    this.send({type: 'button', button: 'left', down: this.locked});
    this.el.classList.toggle('dragging', this.locked); this.label(this.locked ? '拖动已锁定' : '自在滑动');
  }
  reset() {
    clearTimeout(this.hold);
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null; this.pending = null; this.points.clear();
    this.dragging = false; this.setLocked(false);
    this.send({type: 'release'}); this.el.classList.remove('dragging'); this.label('自在滑动');
  }
}

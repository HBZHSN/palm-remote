import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Touchpad} from '../web/gestures.js';
globalThis.requestAnimationFrame = fn => setTimeout(fn, 0);
globalThis.cancelAnimationFrame = clearTimeout;
function fixture(options = {}) {
  const events = [];
  const element = {addEventListener(){}, setPointerCapture(){}, classList: {add(){}, remove(){}, toggle(){}}};
  const pad = new Touchpad(element, e => events.push(e), options);
  const event = (id, x = 0, y = 0) => ({pointerId: id, clientX: x, clientY: y, preventDefault(){}});
  return {pad, events, event};
}
test('direct tap positions before click and ignores black bars', () => {
  const {pad, events, event} = fixture({direct: () => true, position: e => e.clientX < 0 ? null : {screen: 'second', x: .5, y: .25}});
  pad.down(event(1, -1)); pad.up(event(1, -1)); assert.equal(events.length, 0);
  pad.down(event(1, 50)); pad.up(event(1, 50));
  assert.deepEqual(events, [{type: 'position', screen: 'second', x: .5, y: .25}, {type: 'click', button: 'left'}]);
  events.length = 0;
  pad.down(event(1)); pad.down(event(2)); pad.move(event(2, 0, 30)); pad.up(event(1)); pad.up(event(2));
  assert.deepEqual(events.map(e => e.type), ['position', 'scroll']);
});

test('direct long press drag positions and releases', async () => {
  const {pad, events, event} = fixture({direct: () => true, position: e => ({screen: 'left', x: e.clientX / 100, y: 0})});
  pad.down(event(1, 10)); await new Promise(resolve => setTimeout(resolve, 450));
  pad.move(event(1, 50)); pad.up(event(1, 50));
  assert.deepEqual(events.map(e => e.type), ['position', 'button', 'position', 'button']);
  assert.equal(events.at(-1).down, false);
});

test('single tap and two taps produce clicks', () => {
  const {pad, events, event} = fixture();
  for (let i = 0; i < 2; i++) { pad.down(event(1)); pad.up(event(1)); }
  assert.deepEqual(events.map(e => e.type), ['click', 'click']);
});
test('two finger tap is right click; scroll never clicks or moves after first finger lifts', () => {
  const {pad, events, event} = fixture();
  pad.down(event(1)); pad.down(event(2)); pad.up(event(1)); pad.up(event(2));
  assert.equal(events[0].button, 'right'); events.length = 0;
  pad.down(event(1)); pad.down(event(2)); pad.move(event(1, 0, 25)); pad.up(event(1)); pad.move(event(2, 0, 25)); pad.up(event(2));
  assert.deepEqual(events.map(e => e.type), ['scroll']);
});
test('long press drag releases and cancellation clears lock', async () => {
  const {pad, events, event} = fixture();
  pad.down(event(1)); await new Promise(resolve => setTimeout(resolve, 450));
  pad.move(event(1, 25, 0)); pad.up(event(1));
  assert.deepEqual(events.map(e => e.type), ['button', 'move', 'button']);
  assert.equal(events[0].down, true); assert.equal(events[2].down, false);
  pad.toggleLock(); pad.reset(); assert.equal(pad.locked, false); assert.equal(events.at(-1).type, 'release');
});
test('moving cancels long press and pointer cancellation produces no late movement', async () => {
  const {pad, events, event} = fixture();
  pad.down(event(1)); pad.move(event(1, 20, 0)); pad.reset();
  await new Promise(resolve => setTimeout(resolve, 450));
  assert.deepEqual(events.map(e => e.type), ['release']);
});

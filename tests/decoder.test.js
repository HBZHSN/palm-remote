import test from 'node:test';
import assert from 'node:assert/strict';
import {FrameDecoder, LatestFrame} from '../web/decoder.js';

class DecoderStub {
  static instances = [];
  constructor(callbacks) { this.callbacks = callbacks; this.state = 'unconfigured'; DecoderStub.instances.push(this); }
  configure(config) { this.config = config; this.state = 'configured'; }
  decode(chunk) { this.chunk = chunk; }
  close() { this.state = 'closed'; }
}
const meta = (generation = 1, key = true) => ({generation, video: {
  codec: 'avc1.42c02a', width: 1920, height: 1080, timestamp: 16667, key
}});
const nextTask = () => new Promise(resolve => setImmediate(resolve));
test.beforeEach(() => {
  DecoderStub.instances = [];
  globalThis.VideoDecoder = DecoderStub;
  globalThis.EncodedVideoChunk = class { constructor(value) { Object.assign(this, value); } };
});
test.afterEach(() => { delete globalThis.VideoDecoder; delete globalThis.EncodedVideoChunk; });

test('H.264 output resolves a frame; new generation requires a key frame', async () => {
  const decoder = new FrameDecoder();
  const decoded = decoder.decode(new Blob(['packet']), meta());
  await nextTask();
  const native = DecoderStub.instances[0];
  assert.equal(native.chunk.type, 'key');
  const frame = {timestamp: 16667, close() {}};
  native.callbacks.output(frame);
  assert.equal(await decoded, frame);
  await assert.rejects(decoder.decode(new Blob(['delta']), meta(2, false)), /Key frame/);
  assert.equal(native.state, 'closed');
  decoder.close();
});

test('disconnect releases decoder and rejects pending decode', async () => {
  const decoder = new FrameDecoder();
  const decoded = decoder.decode(new Blob(['packet']), meta());
  const rejection = assert.rejects(decoded, /closed/);
  await nextTask();
  decoder.close();
  await rejection;
  let released = false;
  DecoderStub.instances[0].callbacks.output({close() { released = true; }});
  assert.equal(released, true);
});

test('decode errors propagate, and missing WebCodecs is unsupported', async () => {
  const decoder = new FrameDecoder();
  const decoded = decoder.decode(new Blob(['packet']), meta());
  const rejection = assert.rejects(decoded, /Unsupported codec/);
  await nextTask();
  DecoderStub.instances[0].callbacks.error(new Error('Unsupported codec'));
  await rejection;
  decoder.close();
  delete globalThis.VideoDecoder;
  assert.equal(FrameDecoder.supported, false);
});

test('multiple hardware decodes may be in flight without waiting for paint', async () => {
  const decoder = new FrameDecoder();
  const first = decoder.decode(new ArrayBuffer(4), meta());
  const secondMeta = meta(1, false); secondMeta.video.timestamp = 33334;
  const second = decoder.decode(new ArrayBuffer(4), secondMeta);
  const native = DecoderStub.instances[0];
  assert.equal(decoder.pending.size, 2);
  const frame2 = {timestamp: 33334, close() {}};
  native.callbacks.output(frame2);
  assert.equal(await second, frame2);
  const frame1 = {timestamp: 16667, close() {}};
  native.callbacks.output(frame1);
  assert.equal(await first, frame1);
  decoder.close();
});

test('a slow display draws only the newest frame and releases replaced surfaces', () => {
  const scheduled = [], drawn = [], closed = [];
  const renderer = new LatestFrame(frame => drawn.push(frame.id), callback => scheduled.push(callback));
  for (let id = 0; id < 10; id++) renderer.offer({id, close() { closed.push(id); }}, {});
  assert.equal(scheduled.length, 1);
  assert.deepEqual(closed, [0,1,2,3,4,5,6,7,8]);
  scheduled.shift()();
  assert.deepEqual(drawn, [9]);
  assert.equal(closed.length, 10);
});

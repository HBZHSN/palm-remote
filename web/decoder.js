// Submit compressed frames without serializing decode behind browser paint.
export class FrameDecoder {
  constructor() { this.decoder = null; this.pending = new Map(); this.config = ''; this.epoch = 0; }
  static get supported() { return typeof VideoDecoder !== 'undefined' && typeof EncodedVideoChunk !== 'undefined'; }
  close() {
    this.epoch++;
    if (this.decoder && this.decoder.state !== 'closed') this.decoder.close();
    this.decoder = null; this.config = '';
    const error = new Error('Decoder closed'); error.name = 'AbortError';
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }
  decode(data, meta) {
    if (data instanceof Blob) return data.arrayBuffer().then(bytes => this.decode(bytes, meta));
    const v = meta.video;
    const config = `${v.codec}/${v.width}/${v.height}/${meta.generation}`;
    if (this.config !== config || !this.decoder || this.decoder.state === 'closed') {
      this.close();
      if (!v.key) return Promise.reject(new Error('Key frame required'));
      const epoch = this.epoch;
      this.decoder = new VideoDecoder({
        output: frame => {
          if (this.epoch !== epoch) { frame.close(); return; }
          const pending = this.pending.get(frame.timestamp);
          this.pending.delete(frame.timestamp);
          if (pending) pending.resolve(frame); else frame.close();
        },
        error: error => {
          if (this.epoch !== epoch) return;
          for (const pending of this.pending.values()) pending.reject(error);
          this.pending.clear();
        }
      });
      try {
        this.decoder.configure({codec: v.codec, codedWidth: v.width, codedHeight: v.height,
                                optimizeForLatency: true, hardwareAcceleration: 'no-preference'});
      } catch (error) { this.close(); return Promise.reject(error); }
      this.config = config;
    }
    if (this.pending.size >= 8) return Promise.reject(new Error('Decoder queue overflow'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.close(); reject(new Error('Decode timeout')); }, 2000);
      this.pending.set(v.timestamp, {
        resolve: frame => { clearTimeout(timer); resolve(frame); },
        reject: error => { clearTimeout(timer); reject(error); }
      });
      try {
        this.decoder.decode(new EncodedVideoChunk({type: v.key ? 'key' : 'delta', timestamp: v.timestamp, data}));
      } catch (error) { this.pending.delete(v.timestamp); clearTimeout(timer); reject(error); }
    });
  }
}

// The display keeps only the newest decoded image. Every compressed frame is
// still decoded (H.264 reference dependencies), but old display frames are closed.
export class LatestFrame {
  constructor(draw, schedule = callback => requestAnimationFrame(callback)) {
    this.draw = draw; this.schedule = schedule; this.latest = null; this.scheduled = false;
  }
  offer(frame, meta) {
    this.latest?.frame.close();
    this.latest = {frame, meta};
    if (this.scheduled) return;
    this.scheduled = true;
    this.schedule(() => {
      this.scheduled = false;
      const item = this.latest; this.latest = null;
      if (!item) return;
      try { this.draw(item.frame, item.meta); } finally { item.frame.close(); }
    });
  }
  clear() { this.latest?.frame.close(); this.latest = null; }
}

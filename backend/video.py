"""Low-latency H.264 frames; all methods run on the capture executor."""
from fractions import Fraction
import re
import time


class VideoCapture:
    def __init__(self, desktop):
        self.desktop = desktop
        self.camera = None
        self.monitor_id = None
        self.encoder = None
        self.settings = None
        self.index = 0
        self.codec = None

    def _pixels(self, identifier):
        self.desktop.monitor(identifier)
        if self.monitor_id != identifier:
            if self.camera is not None:
                self.camera.release()
            self.camera = None
            self.monitor_id = identifier
        import dxcam
        if self.camera is None:
            # DXGI ordering differs from EnumDisplayMonitors. Match HMONITOR,
            # never index or resolution. This adapter targets pinned DXcam 0.3.
            factory = getattr(dxcam, '__factory')
            for device, outputs in enumerate(factory.outputs):
                for output, desc in enumerate(outputs):
                    desc.update_desc()
                    if str(int(desc.hmonitor)) == identifier:
                        self.camera = dxcam.create(device_idx=device, output_idx=output,
                                                   output_color='BGRA', max_buffer_len=2)
            if self.camera is None:
                raise RuntimeError('Monitor is unavailable through DXGI')
        # Encode synchronously before the next grab, so the DXGI buffer can be
        # borrowed instead of copying another full 4K BGRA image each frame.
        pixels = self.camera.grab(new_frame_only=False, copy=False)
        if pixels is None:
            raise RuntimeError('DXGI desktop frame is unavailable')
        return pixels

    def capture(self, identifier, quality, generation, fps=60):
        import av
        import cv2
        started = time.perf_counter()
        pixels = self._pixels(identifier)
        captured = time.perf_counter()
        height, width = pixels.shape[:2]
        limit, bitrate, qp = {'original': (None, 40_000_000, 16),
                              'high': ((2560, 1440), 28_000_000, 17),
                              'balanced': ((1920, 1080), 20_000_000, 18),
                              'smooth': ((1280, 720), 8_000_000, 22)}[quality]
        scale = min(1, limit[0] / width, limit[1] / height) if limit else 1
        size = (max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2))
        if size != (width, height):
            pixels = cv2.resize(pixels, size, interpolation=cv2.INTER_AREA)
        scaled = time.perf_counter()
        settings = (identifier, quality, generation, size, fps)
        if settings != self.settings:
            self.encoder = None
            encoder = av.CodecContext.create('h264_nvenc', 'w')
            encoder.width, encoder.height = size
            encoder.pix_fmt = 'yuv420p'
            encoder.time_base = Fraction(1, fps)
            encoder.framerate = Fraction(fps, 1)
            encoder.max_b_frames = 0
            encoder.gop_size = fps * 10
            encoder.bit_rate = bitrate
            encoder.options = {'preset': 'p4', 'tune': 'ull', 'zerolatency': '1',
                               'delay': '0', 'rc': 'vbr', 'cq': str(qp),
                               'maxrate': str(bitrate), 'bufsize': str(bitrate // 8),
                               'profile': 'high', 'rc-lookahead': '0',
                               'spatial-aq': '1', 'aq-strength': '5'}
            # OpenCV's I420 conversion uses limited-range BT.601. Tag the
            # actual matrix instead of letting HD decoders assume BT.709.
            encoder.colorspace = 6
            encoder.color_range = 1
            encoder.color_primaries = 1
            encoder.color_trc = 1
            encoder.open()
            self.encoder = encoder
            self.settings = settings
        # Parallel native color conversion avoids PyAV's slow single-threaded
        # BGRA -> YUV conversion at 4K. No B frames or lookahead buffering.
        frame = av.VideoFrame.from_ndarray(cv2.cvtColor(pixels, cv2.COLOR_BGRA2YUV_I420), format='yuv420p')
        converted = time.perf_counter()
        frame.pts = self.index
        self.index += 1
        packets = self.encoder.encode(frame)
        encoded = time.perf_counter()
        if not packets:
            raise RuntimeError('Encoder buffered a frame despite low-latency settings')
        payload = b''.join(bytes(packet) for packet in packets)
        key = any(packet.is_keyframe for packet in packets)
        if key:
            for nal in re.split(b'\x00\x00\x01', payload):
                if len(nal) >= 4 and nal[0] & 31 == 7:
                    self.codec = 'avc1.' + nal[1:4].hex()
                    break
        return payload, {'codec': self.codec, 'key': key, 'width': size[0], 'height': size[1],
                         'encoder': 'NVENC',
                         'source_width': width, 'source_height': height,
                         'stages': {'grab_ms': round((captured-started)*1000, 2),
                                    'scale_ms': round((scaled-captured)*1000, 2),
                                    'convert_ms': round((converted-scaled)*1000, 2),
                                    'encode_ms': round((encoded-converted)*1000, 2)},
                         'timestamp': int(started * 1_000_000)}

    def close(self):
        self.encoder = None
        self.settings = None
        if self.camera is not None:
            self.camera.release()
            self.camera = None

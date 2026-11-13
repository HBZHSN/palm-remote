"""Optional NVENC text-detail comparison with the previous encoder settings."""
import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import av
import cv2
import numpy as np
from backend.video import VideoCapture


def main():
    image = np.full((1080, 1920, 4), (28, 28, 28, 255), dtype=np.uint8)
    for y in range(25, 1070, 24):
        cv2.putText(image, 'Remote desktop 1234567890: small text, code, symbols -> += [] {} / ABC',
                    (12, y), cv2.FONT_HERSHEY_SIMPLEX, .52, (230, 235, 240, 255), 1, cv2.LINE_AA)
        cv2.putText(image, 'COLOR text RGB 0123456789', (1000, y), cv2.FONT_HERSHEY_SIMPLEX,
                    .6, ((y * 3) % 255, 180, 250, 255), 1, cv2.LINE_AA)
    source = cv2.cvtColor(image, cv2.COLOR_BGRA2YUV_I420)
    old = av.CodecContext.create('h264_nvenc', 'w')
    old.width, old.height, old.pix_fmt = 1920, 1080, 'yuv420p'
    old.time_base, old.framerate = Fraction(1, 60), Fraction(60, 1)
    old.max_b_frames, old.gop_size, old.bit_rate = 0, 120, 12_000_000
    old.options = {'preset': 'p1', 'tune': 'ull', 'zerolatency': '1', 'delay': '0',
                   'rc': 'vbr', 'cq': '23', 'maxrate': '12000000', 'bufsize': '3000000', 'profile': 'baseline'}
    old.open()
    current = VideoCapture(None)
    current._pixels = lambda identifier: image
    results = {}
    for label in ('previous', 'current'):
        decoder = av.CodecContext.create('h264', 'r')
        sizes = []
        for i in range(30):
            if label == 'previous':
                frame = av.VideoFrame.from_ndarray(source, format='yuv420p')
                frame.pts = i
                payload = b''.join(bytes(packet) for packet in old.encode(frame))
            else:
                payload, _ = current.capture('test', 'balanced', 1)
            sizes.append(len(payload))
            decoded = decoder.decode(av.Packet(payload))[-1].to_ndarray(format='yuv420p')
            if i == 0:
                first = cv2.PSNR(source[:1080], decoded[:1080])
        results[label] = {'first_frame_luma_psnr_db': round(first, 2),
                          'settled_luma_psnr_db': round(cv2.PSNR(source[:1080], decoded[:1080]), 2),
                          'total_kb_30_frames': round(sum(sizes) / 1024, 1)}
    current.close()
    print(json.dumps(results, indent=2))
    assert results['current']['first_frame_luma_psnr_db'] > results['previous']['first_frame_luma_psnr_db']
    assert results['current']['settled_luma_psnr_db'] > results['previous']['settled_luma_psnr_db']


if __name__ == '__main__':
    main()

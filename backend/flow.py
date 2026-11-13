"""Bounded video flow control sized to the measured decode round trip."""
from collections import deque
import math
import time


class VideoFlow:
    MAX_FRAMES = 8
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.pending = {}
        self.samples = deque(maxlen=20)
        self.window = 3
        self.last_ack_ms = 0

    def sent(self, number, size):
        self.pending[number] = (self.clock(), size)

    def acknowledge(self, number, fps):
        item = self.pending.pop(number, None)
        if item is None:
            return False
        elapsed = max(0, self.clock() - item[0])
        self.last_ack_ms = elapsed * 1000
        self.samples.append(elapsed)
        # Use the recent low-delay baseline, not a congestion spike, to size
        # the pipeline. One extra frame covers pacing jitter. Never expand
        # indefinitely in response to a slow client or a blocked TCP socket.
        baseline = min(self.samples)
        self.window = max(2, min(self.MAX_FRAMES, math.ceil(baseline * fps) + 1))
        return True

    def can_send(self):
        if len(self.pending) >= self.window:
            return False
        if sum(item[1] for item in self.pending.values()) >= self.MAX_BYTES:
            return False
        baseline = min(self.samples) if self.samples else .05
        oldest = min((item[0] for item in self.pending.values()), default=self.clock())
        return self.clock() - oldest < max(.15, baseline * 2.5)

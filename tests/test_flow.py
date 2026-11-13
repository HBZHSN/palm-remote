import unittest
from backend.flow import VideoFlow


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.flow = VideoFlow(clock=lambda: self.now)

    def test_window_covers_round_trip_without_unbounded_growth(self):
        self.flow.sent(1, 1000)
        self.now = .08
        self.assertTrue(self.flow.acknowledge(1, 60))
        self.assertEqual(self.flow.window, 6)
        for number in range(2, 8):
            self.flow.sent(number, 1000)
        self.assertFalse(self.flow.can_send())
        self.assertFalse(self.flow.acknowledge(-1, 60))
        self.now = .16
        self.flow.acknowledge(2, 60)
        self.assertTrue(self.flow.can_send())

    def test_spikes_do_not_expand_window_and_stalled_frames_block_production(self):
        self.flow.sent(1, 1000)
        self.now = .01
        self.flow.acknowledge(1, 60)
        self.flow.sent(2, 1000)
        self.now = .5
        self.assertFalse(self.flow.can_send())
        self.flow.acknowledge(2, 60)
        self.assertEqual(self.flow.window, 2)
        self.assertTrue(self.flow.can_send())

    def test_byte_budget_bounds_high_detail_keyframes(self):
        self.flow.sent(1, VideoFlow.MAX_BYTES)
        self.assertFalse(self.flow.can_send())

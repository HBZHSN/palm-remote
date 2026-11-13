"""Desktop capture and physical monitor coordinates (interactive Windows session)."""
import ctypes
import math
import sys
from ctypes import wintypes


class Desktop:
    def __init__(self):
        if sys.platform == 'win32':
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    def monitors(self):
        user32 = ctypes.windll.user32
        result = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                         wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def collect(handle, dc, rect, data):
            r = rect.contents
            result.append(dict(id=str(int(handle)), left=r.left, top=r.top,
                               width=r.right-r.left, height=r.bottom-r.top))
            return True

        if not user32.EnumDisplayMonitors(None, None, callback_type(collect), 0):
            raise OSError('无法读取显示器')
        return sorted(result, key=lambda m: (m['left'], m['top']))

    def monitor(self, identifier):
        for monitor in self.monitors():
            if monitor['id'] == identifier:
                return monitor
        raise ValueError('显示器已断开，请重新选择')

    def cursor(self, identifier):
        m = self.monitor(identifier)
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise OSError('Cannot read cursor position')
        x, y = point.x-m['left'], point.y-m['top']
        return {'x': x/max(1, m['width']-1), 'y': y/max(1, m['height']-1),
                'visible': 0 <= x < m['width'] and 0 <= y < m['height']}

    def position(self, controller, identifier, x, y):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or not 0 <= v <= 1 for v in (x, y)):
            raise ValueError('Invalid screen coordinates')
        m = self.monitor(identifier)
        monitors = self.monitors()
        left = min(v['left'] for v in monitors)
        top = min(v['top'] for v in monitors)
        width = max(v['left']+v['width'] for v in monitors)-left
        height = max(v['top']+v['height'] for v in monitors)-top
        controller.mouse(0xC001, round((m['left']+x*(m['width']-1)-left)*65535/max(1, width-1)),
                         round((m['top']+y*(m['height']-1)-top)*65535/max(1, height-1)))

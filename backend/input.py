"""Small Windows SendInput adapter. No clipboard access or shell execution."""
import ctypes
import math
import sys
from ctypes import wintypes

KEYS = {'enter': 0x0D, 'escape': 0x1B, 'backspace': 0x08, 'tab': 9,
        'space': 0x20, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
        'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
        'delete': 0x2E, 'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD,
        'play_pause': 0xB3, 'next_track': 0xB0, 'prev_track': 0xB1,
        'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5B,
        **{c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'}}
SHORTCUTS = {'copy': ['ctrl', 'c'], 'paste': ['ctrl', 'v'], 'undo': ['ctrl', 'z'],
             'select_all': ['ctrl', 'a'], 'switch': ['alt', 'tab'],
             'desktop': ['win', 'd'], 'close': ['alt', 'f4']}
KEYS['f4'] = 0x73


class Mouse(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class Keyboard(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class Payload(ctypes.Union):
    _fields_ = [('mi', Mouse), ('ki', Keyboard)]


class Input(ctypes.Structure):
    _anonymous_ = ('payload',)
    _fields_ = [('type', wintypes.DWORD), ('payload', Payload)]


def number(value, limit=1000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid movement')
    return round(max(-limit, min(limit, value)))


class Controller:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.held = set()
        self.events = []
        if not dry_run:
            if sys.platform != 'win32':
                raise RuntimeError('Desktop control requires Windows; use --dry-run for development.')
            self.user32 = ctypes.WinDLL('user32', use_last_error=True)
            # Capture and SendInput must agree on physical pixels on mixed-DPI desktops.
            self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
            self.user32.SendInput.restype = wintypes.UINT

    def emit(self, event):
        if self.dry_run:
            self.events.append(event)
            self.events = self.events[-256:]
        elif self.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise OSError('Windows refused input; elevated windows cannot be controlled.')

    def mouse(self, flags, dx=0, dy=0, data=0):
        self.emit(Input(type=0, mi=Mouse(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)))

    def button(self, button, down):
        if button not in ('left', 'right', 'middle'):
            raise ValueError('Unknown mouse button')
        if down == (button in self.held):
            return
        self.mouse({'left': 2, 'right': 8, 'middle': 32}[button] * (1 if down else 2))
        self.held.add(button) if down else self.held.discard(button)

    def release(self):
        for button in list(self.held):
            self.button(button, False)

    def key(self, name, up=False):
        extended = name in {'left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown', 'delete', 'win'}
        self.emit(Input(type=1, ki=Keyboard(KEYS[name], 0, (2 if up else 0) | int(extended), 0, 0)))

    def dispatch(self, data):
        if not isinstance(data, dict):
            raise ValueError('Expected object')
        action = data.get('type')
        if action == 'move':
            self.mouse(1, number(data.get('dx')), number(data.get('dy')))
        elif action == 'scroll':
            dx, dy = number(data.get('dx', 0)), number(data.get('dy', 0))
            if dy:
                self.mouse(0x800, data=dy)
            if dx:
                self.mouse(0x1000, data=dx)
        elif action == 'button':
            if type(data.get('down')) is not bool:
                raise ValueError('Invalid button state')
            self.button(data.get('button'), data['down'])
        elif action == 'click':
            button = data.get('button', 'left')
            if button in self.held:
                raise ValueError('Button is held')
            self.button(button, True)
            self.button(button, False)
        elif action in ('key', 'shortcut'):
            name = data.get('key')
            names = SHORTCUTS.get(name) if action == 'shortcut' else ([name] if name in KEYS else None)
            if not names:
                raise ValueError('Unknown key')
            pressed = []
            try:
                for name in names:
                    self.key(name)
                    pressed.append(name)
            finally:
                for name in reversed(pressed):
                    self.key(name, True)
        elif action == 'text':
            value = data.get('text')
            if not isinstance(value, str) or not 1 <= len(value) <= 2000:
                raise ValueError('Text must contain 1–2000 characters')
            raw = value.encode('utf-16-le')
            for i in range(0, len(raw), 2):
                unit = int.from_bytes(raw[i:i + 2], 'little')
                self.emit(Input(type=1, ki=Keyboard(0, unit, 4, 0, 0)))
                self.emit(Input(type=1, ki=Keyboard(0, unit, 6, 0, 0)))
        elif action == 'release':
            self.release()
        else:
            raise ValueError('Unknown command')

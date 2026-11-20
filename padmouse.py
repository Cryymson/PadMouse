"""
Padmouse - use an Xbox-style (XInput) controller as a keyboard + mouse replacement.
Windows only. Pure standard library. Lives in the system tray.

Controls (edit the CONFIG / mapping tables below to taste)
  Left stick ........ mouse            Right stick ....... scroll (vertical + horizontal)
  RT ................ left click       LT ................ right click
  R3 (stick click) .. middle click     L3 (stick click) .. toggle precision (slow) mode
  A / B / X ......... Enter / Esc / Backspace
  Y ................. on-screen keyboard
  D-pad ............. arrow keys (with key repeat)
  LB / RB ........... browser Back / Forward
  Start ............. Windows key      Back/Select ....... Alt+Tab
  Hold Back+Start 1s  enable / disable (rumble once = off, twice = on)

Tray icon: double-click = enable/disable, right-click = menu.
"""
import ctypes
import math
import os
import sys
import threading
import time
from ctypes import wintypes as w

if os.name != "nt":
    sys.exit("Padmouse only runs on Windows.")

import winreg

# ----------------------------- CONFIG ---------------------------------------
MOUSE_SPEED      = 1500    # pixels/second at full stick deflection
PRECISION_FACTOR = 0.30    # speed multiplier while precision mode is on
MOUSE_CURVE      = 2.0     # 1 = linear, 2 = fine control near centre, fast at edge
MOUSE_DEADZONE   = 0.16
SCROLL_SPEED     = 14.0    # wheel notches/second at full deflection
SCROLL_DEADZONE  = 0.25
TRIGGER_ON, TRIGGER_OFF = 100, 60   # 0-255, with hysteresis
REPEAT_DELAY, REPEAT_RATE = 0.35, 0.045   # d-pad key repeat (seconds)
POLL_ACTIVE, POLL_IDLE, IDLE_AFTER = 0.004, 0.008, 3.0
TOGGLE_HOLD      = 1.0     # seconds to hold Back+Start
APP_NAME         = "Padmouse"
# -----------------------------------------------------------------------------

# XInput button masks
UP, DOWN, LEFT, RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
START, BACK, L3, R3 = 0x0010, 0x0020, 0x0040, 0x0080
LB, RB = 0x0100, 0x0200
BTN_A, BTN_B, BTN_X, BTN_Y = 0x1000, 0x2000, 0x4000, 0x8000
COMBO = START | BACK

# Virtual-key codes
VK_BACKSPACE, VK_TAB, VK_ENTER, VK_ALT, VK_ESC = 0x08, 0x09, 0x0D, 0x12, 0x1B
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_LWIN, VK_BROWSER_BACK, VK_BROWSER_FWD = 0x5B, 0xA6, 0xA7
EXTENDED = {VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_LWIN}

# Mapping tables: button mask -> key (held while button is held)
HOLD_KEYS = {
    BTN_A: VK_ENTER, BTN_B: VK_ESC, BTN_X: VK_BACKSPACE,
    LB: VK_BROWSER_BACK, RB: VK_BROWSER_FWD,
}
DPAD_KEYS = {UP: VK_UP, DOWN: VK_DOWN, LEFT: VK_LEFT, RIGHT: VK_RIGHT}


# ---------------------------- shared state ----------------------------------
class Shared:
    enabled = True
    connected = False
    quit = False

shared = Shared()
HWND = None          # tray window handle (set in main)
ICON = None


def tooltip():
    if not shared.enabled:
        return f"{APP_NAME} - disabled"
    return f"{APP_NAME} - " + ("active" if shared.connected else "no controller")


# ---------------------------- XInput bindings --------------------------------
class GAMEPAD(ctypes.Structure):
    _fields_ = [("buttons", w.WORD), ("lt", ctypes.c_ubyte), ("rt", ctypes.c_ubyte),
                ("lx", ctypes.c_short), ("ly", ctypes.c_short),
                ("rx", ctypes.c_short), ("ry", ctypes.c_short)]

class XSTATE(ctypes.Structure):
    _fields_ = [("packet", w.DWORD), ("pad", GAMEPAD)]

class XVIB(ctypes.Structure):
    _fields_ = [("left", w.WORD), ("right", w.WORD)]

xi = None
for _name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
    try:
        xi = ctypes.WinDLL(_name)
        break
    except OSError:
        continue
if xi is None:
    sys.exit("Could not load XInput.")
xi.XInputGetState.argtypes = [w.DWORD, ctypes.POINTER(XSTATE)]
xi.XInputGetState.restype = w.DWORD
xi.XInputSetState.argtypes = [w.DWORD, ctypes.POINTER(XVIB)]
xi.XInputSetState.restype = w.DWORD

# ---------------------------- SendInput bindings -----------------------------
ULONG_PTR = ctypes.c_size_t

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]

class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _U)]

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")

user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT

MOVE, LDOWN, LUP, RDOWN, RUP, MDOWN, MUP = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40
WHEEL, HWHEEL = 0x800, 0x1000
MOUSE_BUTTONS = {"L": (LDOWN, LUP), "R": (RDOWN, RUP), "M": (MDOWN, MUP)}

def send(events):
    if events:
        n = len(events)
        user32.SendInput(n, (INPUT * n)(*events), ctypes.sizeof(INPUT))

def key_ev(vk, down):
    flags = (0 if down else 0x2) | (0x1 if vk in EXTENDED else 0)
    e = INPUT(type=1)
    e.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return e

def mouse_ev(flags, dx=0, dy=0, data=0):
    e = INPUT(type=0)
    e.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return e

# Track what we're holding so we never leave a key/button stuck.
held_keys, held_mouse = set(), set()

def set_key(vk, down):
    if down and vk not in held_keys:
        held_keys.add(vk); send([key_ev(vk, True)])
    elif not down and vk in held_keys:
        held_keys.discard(vk); send([key_ev(vk, False)])

def set_mouse(name, down):
    if down and name not in held_mouse:
        held_mouse.add(name); send([mouse_ev(MOUSE_BUTTONS[name][0])])
    elif not down and name in held_mouse:
        held_mouse.discard(name); send([mouse_ev(MOUSE_BUTTONS[name][1])])

def tap(vk):
    send([key_ev(vk, True), key_ev(vk, False)])

def release_all():
    for vk in list(held_keys):
        set_key(vk, False)
    for m in list(held_mouse):
        set_mouse(m, False)

def alt_tab():
    send([key_ev(VK_ALT, True), key_ev(VK_TAB, True), key_ev(VK_TAB, False), key_ev(VK_ALT, False)])

def open_osk():
    try:
        os.startfile("osk.exe")
    except OSError:
        pass


# ---------------------------- input helpers ---------------------------------
def stick(x, y, deadzone, curve):
    """Radial deadzone + response curve. Returns (-1..1, -1..1)."""
    fx, fy = x / 32768.0, y / 32768.0
    mag = math.hypot(fx, fy)
    if mag < deadzone:
        return 0.0, 0.0
    scaled = (min(mag, 1.0) - deadzone) / (1.0 - deadzone)
    k = (scaled ** curve) / mag
    return fx * k, fy * k

def rumble(idx, strength=40000, secs=0.1):
    try:
        xi.XInputSetState(idx, ctypes.byref(XVIB(strength, strength)))
        time.sleep(secs)
        xi.XInputSetState(idx, ctypes.byref(XVIB(0, 0)))
    except Exception:
        pass

def find_pad():
    s = XSTATE()
    for i in range(4):
        if xi.XInputGetState(i, ctypes.byref(s)) == 0:
            return i
    return None


# ---------------------------- worker thread ---------------------------------
WM_STATUS = 0x8002

def notify():
    if HWND:
        user32.PostMessageW(HWND, WM_STATUS, 0, 0)

def input_loop():
    prev, rt_down, lt_down = 0, False, False
    repeat_at = {}
    acc = [0.0, 0.0, 0.0, 0.0]                  # mouse x/y, scroll x/y remainders
    precision = False
    combo_start, combo_touched, combo_fired = None, False, False
    idx, last_scan = None, 0.0
    last = last_active = time.perf_counter()
    st = XSTATE()

    while not shared.quit:
        now = time.perf_counter()

        # --- (re)connect ---
        if idx is None:
            if now - last_scan < 1.0:
                time.sleep(0.1)
                continue
            last_scan = now
            idx = find_pad()
            if idx is None:
                continue
            shared.connected = True
            notify()
            last = last_active = now

        if xi.XInputGetState(idx, ctypes.byref(st)) != 0:
            release_all()
            idx, prev = None, 0
            shared.connected = False
            notify()
            continue

        p = st.pad
        b = p.buttons
        pressed, released = b & ~prev, prev & ~b
        dt = min(now - last, 0.05)
        last = now

        # --- enable/disable combo (Back + Start held) ---
        if (b & COMBO) == COMBO:
            combo_touched = True
            if combo_start is None:
                combo_start = now
            elif not combo_fired and now - combo_start >= TOGGLE_HOLD:
                combo_fired = True
                shared.enabled = not shared.enabled
                release_all()
                notify()
                rumble(idx)
                if shared.enabled:
                    time.sleep(0.08)
                    rumble(idx)
        else:
            combo_start = None
        # Start/Back act on release, and only if they weren't part of the combo
        if not combo_touched and shared.enabled:
            if released & START:
                tap(VK_LWIN)
            if released & BACK:
                alt_tab()
        if not (b & COMBO):
            combo_touched = combo_fired = False

        if not shared.enabled:
            release_all()
            prev = b
            time.sleep(POLL_IDLE)
            continue

        # --- mouse buttons from triggers (with hysteresis) ---
        rt_down = p.rt > TRIGGER_ON or (rt_down and p.rt > TRIGGER_OFF)
        lt_down = p.lt > TRIGGER_ON or (lt_down and p.lt > TRIGGER_OFF)
        set_mouse("L", rt_down)
        set_mouse("R", lt_down)
        set_mouse("M", bool(b & R3))

        # --- held keys ---
        for mask, vk in HOLD_KEYS.items():
            set_key(vk, bool(b & mask))

        # --- one-shot actions ---
        if pressed & L3:
            precision = not precision
        if pressed & BTN_Y:
            open_osk()

        # --- d-pad with auto-repeat ---
        for mask, vk in DPAD_KEYS.items():
            if pressed & mask:
                tap(vk); repeat_at[mask] = now + REPEAT_DELAY
            elif b & mask and now >= repeat_at.get(mask, 1e18):
                tap(vk); repeat_at[mask] = now + REPEAT_RATE

        # --- mouse move + scroll, batched into a single SendInput ---
        events = []
        mx, my = stick(p.lx, p.ly, MOUSE_DEADZONE, MOUSE_CURVE)
        sx, sy = stick(p.rx, p.ry, SCROLL_DEADZONE, 1.5)
        speed = MOUSE_SPEED * (PRECISION_FACTOR if precision else 1.0)

        acc[0] += mx * speed * dt
        acc[1] -= my * speed * dt            # stick up = cursor up
        ix, iy = int(acc[0]), int(acc[1])
        acc[0] -= ix; acc[1] -= iy
        if ix or iy:
            events.append(mouse_ev(MOVE, ix, iy))

        acc[2] += sx * SCROLL_SPEED * 120 * dt
        acc[3] += sy * SCROLL_SPEED * 120 * dt
        wx, wy = int(acc[2]), int(acc[3])
        acc[2] -= wx; acc[3] -= wy
        if wy:
            events.append(mouse_ev(WHEEL, data=wy))
        if wx:
            events.append(mouse_ev(HWHEEL, data=wx))
        send(events)

        # --- adaptive polling: fast when in use, relaxed when idle ---
        if b or mx or my or sx or sy or p.lt > 10 or p.rt > 10:
            last_active = now
        prev = b
        time.sleep(POLL_ACTIVE if now - last_active < IDLE_AFTER else POLL_IDLE)

    release_all()


# ---------------------------- autostart (registry) ---------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def launch_cmd():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{os.path.abspath(__file__)}"'

def autostart_on():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False

def set_autostart(on):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_cmd())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
    except OSError:
        pass


# ---------------------------- tray icon (Win32) ------------------------------
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]

class GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
                ("uFlags", w.UINT), ("uCallbackMessage", w.UINT), ("hIcon", w.HICON),
                ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                ("szInfo", w.WCHAR * 256), ("uTimeout", w.UINT),
                ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", w.HICON)]

user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = w.ATOM
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.CreateWindowExW.restype = w.HWND
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
user32.GetMessageW.restype = w.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.CreatePopupMenu.restype = w.HMENU
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [w.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
user32.RegisterWindowMessageW.restype = w.UINT
user32.LoadIconW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = w.HICON
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
shell32.ExtractIconW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT]
shell32.ExtractIconW.restype = w.HICON
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
kernel32.GetModuleHandleW.restype = w.HINSTANCE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
kernel32.CreateMutexW.restype = w.HANDLE

WM_DESTROY, WM_NULL, WM_TRAY = 0x0002, 0x0000, 0x8001
WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0, 0x8, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
TASKBAR_CREATED = 0xFFFFFFFF


def tray(action):
    nid = NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
    nid.hWnd = HWND
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = ICON
    nid.szTip = tooltip()[:127]
    shell32.Shell_NotifyIconW(action, ctypes.byref(nid))

def show_menu(hwnd):
    m = user32.CreatePopupMenu()
    user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if shared.enabled else 0), 1, "Enabled")
    user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if autostart_on() else 0), 2, "Start with Windows")
    user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(m, MF_STRING, 3, "Quit")
    pt = w.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)
    cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    user32.DestroyMenu(m)
    if cmd == 1:
        shared.enabled = not shared.enabled
        tray(NIM_MODIFY)
    elif cmd == 2:
        set_autostart(not autostart_on())
    elif cmd == 3:
        user32.PostQuitMessage(0)

def wnd_proc(hwnd, msg, wp, lp):
    if msg == WM_TRAY:
        if lp == WM_RBUTTONUP:
            show_menu(hwnd)
        elif lp == WM_LBUTTONDBLCLK:
            shared.enabled = not shared.enabled
            tray(NIM_MODIFY)
        return 0
    if msg == WM_STATUS:
        tray(NIM_MODIFY)
        return 0
    if msg == TASKBAR_CREATED:          # explorer.exe restarted -> re-add icon
        tray(NIM_ADD)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)

_wndproc = WNDPROC(wnd_proc)            # keep a reference so it isn't garbage-collected


def main():
    global HWND, ICON, TASKBAR_CREATED

    # single instance
    mutex = kernel32.CreateMutexW(None, False, "Local\\PadmouseSingleton")
    if ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS
        return

    winmm.timeBeginPeriod(1)             # 1 ms timer resolution -> snappy
    TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")

    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.lpfnWndProc = _wndproc
    wc.hInstance = hinst
    wc.lpszClassName = "PadmouseTrayWindow"
    user32.RegisterClassW(ctypes.byref(wc))
    HWND = user32.CreateWindowExW(0, wc.lpszClassName, APP_NAME, 0, 0, 0, 0, 0,
                                  None, None, hinst, None)

    ICON = shell32.ExtractIconW(hinst, sys.executable, 0)
    if not ICON or ICON == 1:
        ICON = user32.LoadIconW(None, ctypes.c_void_p(32512))   # IDI_APPLICATION
    tray(NIM_ADD)

    worker = threading.Thread(target=input_loop, daemon=True)
    worker.start()

    msg = w.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    shared.quit = True
    worker.join(timeout=1.0)
    release_all()
    tray(NIM_DELETE)
    winmm.timeEndPeriod(1)
    del mutex


if __name__ == "__main__":
    main()

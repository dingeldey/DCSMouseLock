import sys
import time
from pathlib import Path
import configparser
import ctypes
import platform

import pygame
import pyautogui

CONFIG_FILE = "joystick_mouse.ini"

# ===== Windows virtual desktop + SendInput helpers =====
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

def win_virtual_desktop_rect():
    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return int(vx), int(vy), int(vw), int(vh)

# Monitor enumeration via EnumDisplayMonitors / GetMonitorInfo
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
        ("szDevice", ctypes.c_wchar * 32),
    ]

MONITORINFOF_PRIMARY = 0x00000001
HMONITOR = ctypes.c_void_p
HDC = ctypes.c_void_p
LPRECT = ctypes.POINTER(RECT)
LPARAM = ctypes.c_long

MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, HMONITOR, HDC, LPRECT, LPARAM)

def win_enumerate_monitors():
    """
    Returns a list of dicts:
      {index, x, y, w, h, primary (bool), name (str), hmon (handle)}
    Index order matches EnumDisplayMonitors enumeration.
    """
    user32 = ctypes.windll.user32
    monitors = []

    def _cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        x = mi.rcMonitor.left
        y = mi.rcMonitor.top
        w = mi.rcMonitor.right - mi.rcMonitor.left
        h = mi.rcMonitor.bottom - mi.rcMonitor.top
        primary = bool(mi.dwFlags & MONITORINFOF_PRIMARY)
        name = mi.szDevice
        monitors.append({"hmon": hmon, "x": x, "y": y, "w": w, "h": h,
                         "primary": primary, "name": name})
        return 1  # continue
    cb_func = MONITORENUMPROC(_cb)
    if not user32.EnumDisplayMonitors(None, None, cb_func, 0):
        return []
    # assign indices
    for idx, m in enumerate(monitors):
        m["index"] = idx
    return monitors

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_uint),
                ("dwFlags", ctypes.c_uint),
                ("time", ctypes.c_uint),
                ("dwExtraInfo", ctypes.c_void_p)]

class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint), ("mi", _MOUSEINPUT)]

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

def sendinput_move_absolute_virtual(x_px, y_px):
    """
    Move mouse to absolute (x_px, y_px) in VIRTUAL DESKTOP coordinates.
    Converts to 0..65535 space with virtual desktop origin/size.
    """
    vx, vy, vw, vh = win_virtual_desktop_rect()
    nx = int(round((x_px - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y_px - vy) * 65535 / max(1, vh - 1)))
    inp = _INPUT()
    inp.type = 0  # INPUT_MOUSE
    inp.mi = _MOUSEINPUT(nx, ny, 0,
                         MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                         0, None)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
# =======================================================


def init_pygame():
    pygame.init()
    pygame.joystick.init()
    pygame.event.set_allowed(None)
    pygame.event.set_allowed([
        pygame.QUIT,
        pygame.JOYBUTTONDOWN,
        pygame.JOYBUTTONUP,          # keep for debug
        pygame.JOYDEVICEADDED,
        pygame.JOYDEVICEREMOVED
    ])


def list_devices():
    """
    Print all devices with Index | Buttons | GUID | Name.
    Return (devices, instance_to_index_map)
    """
    count = pygame.joystick.get_count()
    print(f"[INFO] Found game controllers: {count}")
    devices = []
    inst_to_idx = {}
    for i in range(count):
        js = pygame.joystick.Joystick(i); js.init()
        name = js.get_name()
        guid = js.get_guid() if hasattr(js, "get_guid") else "N/A"
        buttons = js.get_numbuttons()
        try:
            instance_id = js.get_instance_id()
        except AttributeError:
            instance_id = js.get_id()
        print(f"  Index={i:2d} | Buttons={buttons:3d} | GUID={guid} | Name='{name}'")
        devices.append((i, name, guid, buttons, instance_id))
        inst_to_idx[instance_id] = i
    return devices, inst_to_idx


def list_monitors_windows():
    mons = win_enumerate_monitors()
    if not mons:
        print("[WARN] Could not enumerate monitors, falling back to primary only.")
        return [{"index": 0, "x": 0, "y": 0, "w": pyautogui.size()[0], "h": pyautogui.size()[1],
                 "primary": True, "name": "PRIMARY"}]
    print("[INFO] Monitors (Windows virtual desktop):")
    for m in mons:
        prim = "Yes" if m["primary"] else "No "
        print(f"  MonIdx={m['index']} | Primary={prim} | x={m['x']} y={m['y']} w={m['w']} h={m['h']} | Name='{m['name']}'")
    return mons


def load_config(path: str):
    if not Path(path).exists():
        print(f"[ERROR] Config file '{path}' not found."); sys.exit(1)

    cfgp = configparser.ConfigParser(
        inline_comment_prefixes=(';', '#'),
        interpolation=None,
        strict=False
    )
    cfgp.read(path, encoding="utf-8")
    if "input" not in cfgp:
        print("[ERROR] Missing [input] section."); sys.exit(1)
    sec = cfgp["input"]

    def getbool(k, d): return sec.get(k, str(d)).strip().lower() in ("1","true","yes","y","on")

    device_guid = sec.get("device_guid", "").strip()
    idx_s = sec.get("device_index", "").strip()
    device_index = int(idx_s) if idx_s.isdigit() else None
    mon_s = sec.get("monitor_index", "").strip()
    monitor_index = int(mon_s) if mon_s.isdigit() else 0

    button = sec.getint("button", fallback=None)
    if button is None:
        print("[ERROR] 'button' is required."); sys.exit(1)

    def f_or_none(k):
        s = sec.get(k, "").strip()
        if s == "": return None
        try: return float(s)
        except: print(f("[ERROR] '{k}' must be float.")); sys.exit(1)

    def i_or_none(k):
        s = sec.get(k, "").strip()
        if s == "": return None
        try: return int(s)
        except: print(f("[ERROR] '{k}' must be int.")); sys.exit(1)

    x_frac, y_frac = f_or_none("x_frac"), f_or_none("y_frac")
    x_px, y_px     = i_or_none("x"), i_or_none("y")

    use_frac = (x_frac is not None and y_frac is not None)
    use_px   = (x_px   is not None and y_px   is not None)
    if not (use_frac or use_px):
        print("[ERROR] Provide x_frac & y_frac or x & y."); sys.exit(1)

    poll_hz = max(10, sec.getint("poll_hz", fallback=250))
    startup_grace_ms = max(0, sec.getint("startup_grace_ms", fallback=200))
    repeat_ms = max(1, sec.getint("repeat_ms", fallback=1000))

    cfg = {
        "device_guid": device_guid,
        "device_index": device_index,
        "monitor_index": monitor_index,
        "button": int(button),
        "x_frac": x_frac, "y_frac": y_frac,
        "x": x_px, "y": y_px,
        "poll_hz": poll_hz,
        "startup_grace_ms": startup_grace_ms,
        "repeat_ms": repeat_ms,
        "use_sendinput": getbool("use_sendinput", platform.system().lower()=="windows"),
        "toggle_feedback": getbool("toggle_feedback", True),
        "log_apply": getbool("log_apply", False),
        "debug_buttons": getbool("debug_buttons", False),
        "wiggle_one_pixel": getbool("wiggle_one_pixel", False),
    }
    return cfg


def open_device_by_guid_or_index(guid: str, idx: int | None):
    gl = (guid or "").lower()
    count = pygame.joystick.get_count()
    if gl:
        for i in range(count):
            js = pygame.joystick.Joystick(i); js.init()
            if hasattr(js, "get_guid") and js.get_guid().lower() == gl:
                return js
    if idx is not None and 0 <= idx < count:
        js = pygame.joystick.Joystick(idx); js.init()
        return js
    return None


def event_device_id(e):
    return getattr(e, "instance_id", getattr(e, "joy", None))

def event_matches_device(e, js) -> bool:
    ev_id = event_device_id(e)
    try:
        js_id = js.get_instance_id()
    except AttributeError:
        js_id = js.get_id()
    return ev_id == js_id


def main():
    pyautogui.FAILSAFE = False
    init_pygame()

    print("===================================================")
    print("  Joystick/Throttle → Mouse Position Repeater (per-monitor)")
    print("===================================================\n")

    devices, inst_to_idx = list_devices(); print()

    cfg = load_config(CONFIG_FILE)

    # Monitors
    if platform.system().lower() == "windows":
        monitors = list_monitors_windows()
    else:
        # Non-Windows fallback: only primary screen known via pyautogui
        sw, sh = pyautogui.size()
        monitors = [{"index": 0, "x": 0, "y": 0, "w": sw, "h": sh, "primary": True, "name": "PRIMARY"}]
        print("[INFO] Non-Windows: using primary screen only.")

    if not monitors:
        print("[ERROR] No monitors found."); sys.exit(1)

    mon_idx = cfg["monitor_index"]
    if not (0 <= mon_idx < len(monitors)):
        print(f"[WARN] monitor_index={mon_idx} out of range. Using 0.")
        mon_idx = 0
    mon = monitors[mon_idx]

    js = open_device_by_guid_or_index(cfg["device_guid"], cfg["device_index"])
    if js is None:
        print("[ERROR] Could not open device (set device_guid or device_index)."); sys.exit(1)

    # Resolve IDs/info
    try: instance_id = js.get_instance_id()
    except AttributeError: instance_id = js.get_id()
    name = js.get_name()
    guid = js.get_guid() if hasattr(js, "get_guid") else "N/A"

    button_index = cfg["button"]
    btns = js.get_numbuttons()
    if not (0 <= button_index < btns):
        print(f"[ERROR] Button #{button_index} out of range for this device (0..{btns-1})."); sys.exit(1)

    # Compute target: relative to SELECTED monitor, then convert to VIRTUAL desktop coords if needed
    if cfg["x_frac"] is not None and cfg["y_frac"] is not None:
        target_x = int(round(mon["x"] + cfg["x_frac"] * mon["w"]))
        target_y = int(round(mon["y"] + cfg["y_frac"] * mon["h"]))
    else:
        target_x = int(mon["x"] + cfg["x"])
        target_y = int(mon["y"] + cfg["y"])

    print(f"[OK] Using device: Index={inst_to_idx.get(instance_id,'?')}, GUID={guid}, Name='{name}'")
    print(f"[OK] Using monitor: MonIdx={mon['index']} ({'Primary' if mon['primary'] else 'Secondary'}) "
          f"x={mon['x']} y={mon['y']} w={mon['w']} h={mon['h']} Name='{mon['name']}'")
    print(f"[OK] Watching Button #{button_index}")
    print(f"[OK] Target (absolute virtual desktop): ({target_x}, {target_y}), "
          f"repeat every {cfg['repeat_ms']} ms\n")

    # --- Single-thread toggle/repeat state ---
    pygame.event.clear()
    grace_until = time.monotonic() + (cfg["startup_grace_ms"] / 1000.0)
    active = False
    last_toggle = time.monotonic()
    debounce_s = 0.15
    last_apply = time.monotonic()
    repeat_interval = cfg["repeat_ms"] / 1000.0
    wiggle_flip = False

    def apply_cursor():
        x = target_x + (1 if (cfg["wiggle_one_pixel"] and wiggle_flip) else 0)
        y = target_y
        if cfg["use_sendinput"] and platform.system().lower() == "windows":
            sendinput_move_absolute_virtual(x, y)
        else:
            pyautogui.moveTo(x, y)

    try:
        while True:
            now = time.monotonic()

            for event in pygame.event.get():
                # Optional debug of ALL button events (with mapped device index)
                if cfg["debug_buttons"] and event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                    edge = "DOWN" if event.type == pygame.JOYBUTTONDOWN else "UP  "
                    ev_dev_id = event_device_id(event)
                    ev_dev_idx = inst_to_idx.get(ev_dev_id, "?")
                    print(f"[DBG] {edge}: dev_index={ev_dev_idx} btn={getattr(event,'button',None)}")

                if now < grace_until:
                    continue

                if event.type == pygame.JOYBUTTONDOWN and event_matches_device(event, js) and event.button == button_index:
                    if now - last_toggle >= debounce_s:
                        active = not active
                        last_toggle = now
                        if cfg["toggle_feedback"]:
                            print(f"[TOGGLE] {'ACTIVE' if active else 'INACTIVE'}")
                        if active:
                            apply_cursor()
                            last_apply = now
                            wiggle_flip = not wiggle_flip

                elif event.type == pygame.JOYDEVICEREMOVED:
                    if hasattr(event, "instance_id") and event.instance_id == instance_id:
                        print("[WARN] Device removed. Exiting."); return

            # Periodic re-apply while ACTIVE
            if active and (now - last_apply) >= repeat_interval:
                apply_cursor()
                last_apply = now
                wiggle_flip = not wiggle_flip
                if cfg["log_apply"]:
                    shown_x = target_x + (1 if (cfg["wiggle_one_pixel"] and wiggle_flip) else 0)
                    print(f"[APPLY] {shown_x},{target_y} @ {now:.3f}")

            time.sleep(1.0 / float(cfg["poll_hz"]))

    except KeyboardInterrupt:
        print("\n[EXIT] User aborted.")
    finally:
        pygame.joystick.quit()
        pygame.quit()


if __name__ == "__main__":
    main()

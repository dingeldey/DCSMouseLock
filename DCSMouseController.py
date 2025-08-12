#!/usr/bin/env python3
# Joystick/Throttle → Mouse Position Repeater (modifier, per-monitor, hold-adjust, recenter)
# Single-thread, Windows-friendly (SendInput), pygame-based
#
# Features
# - Lists all game controllers (index, GUID, buttons)
# - GUID-first, index-second device selection
# - Optional modifier button (same or second device); mark actions with 'M' (e.g., "25M")
# - Choose target monitor; fractions (preferred) or pixels within that monitor
# - Toggle ON/OFF; ON recenters to INI base each time; OFF does not restore cursor (configurable)
# - Continuous hold-based adjust for X/Y at pixels/second velocity
# - Reapply cursor every repeat_ms while active; optional 1px wiggle
# - Debug prints for button edges with correct device index mapping
# - Clamp target to selected monitor or full virtual desktop (configurable)
#
# Requires: pygame, pyautogui
#   pip install pygame pyautogui

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

# Monitor enumeration
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
                ("szDevice", ctypes.c_wchar * 32)]

MONITORINFOF_PRIMARY = 0x00000001
HMONITOR = ctypes.c_void_p
HDC = ctypes.c_void_p
LPRECT = ctypes.POINTER(RECT)
LPARAM = ctypes.c_long
MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, HMONITOR, HDC, LPRECT, LPARAM)

def win_enumerate_monitors():
    user32 = ctypes.windll.user32
    monitors = []
    def _cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        x = mi.rcMonitor.left; y = mi.rcMonitor.top
        w = mi.rcMonitor.right - mi.rcMonitor.left
        h = mi.rcMonitor.bottom - mi.rcMonitor.top
        primary = bool(mi.dwFlags & MONITORINFOF_PRIMARY)
        monitors.append({"hmon": hmon, "x": x, "y": y, "w": w, "h": h,
                         "primary": primary, "name": mi.szDevice})
        return 1
    cb = MONITORENUMPROC(_cb)
    if not user32.EnumDisplayMonitors(None, None, cb, 0):
        return []
    for i, m in enumerate(monitors): m["index"] = i
    return monitors

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def get_cursor_pos_virtual():
    pt = POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)

# SendInput absolute move (virtual desktop)
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                ("time", ctypes.c_uint), ("dwExtraInfo", ctypes.c_void_p)]

class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint), ("mi", _MOUSEINPUT)]

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

def sendinput_move_absolute_virtual(x_px, y_px):
    vx, vy, vw, vh = win_virtual_desktop_rect()
    nx = int(round((x_px - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y_px - vy) * 65535 / max(1, vh - 1)))
    inp = _INPUT()
    inp.type = 0
    inp.mi = _MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, None)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
# =======================================================

def init_pygame():
    pygame.init(); pygame.joystick.init()
    pygame.event.set_allowed(None)
    pygame.event.set_allowed([
        pygame.QUIT,
        pygame.JOYBUTTONDOWN,
        pygame.JOYBUTTONUP,
        pygame.JOYDEVICEADDED,
        pygame.JOYDEVICEREMOVED
    ])

def list_devices():
    count = pygame.joystick.get_count()
    print(f"[INFO] Found game controllers: {count}")
    devices = []; inst_to_idx = {}
    for i in range(count):
        js = pygame.joystick.Joystick(i); js.init()
        name = js.get_name()
        guid = js.get_guid() if hasattr(js, "get_guid") else "N/A"
        buttons = js.get_numbuttons()
        try: instance_id = js.get_instance_id()
        except AttributeError: instance_id = js.get_id()
        print(f"  Index={i:2d} | Buttons={buttons:3d} | GUID={guid} | Name='{name}'")
        devices.append((i, name, guid, buttons, instance_id))
        inst_to_idx[instance_id] = i
    return devices, inst_to_idx

def list_monitors_windows():
    mons = win_enumerate_monitors()
    if not mons:
        print("[WARN] Could not enumerate monitors; using primary from pyautogui.")
        sw, sh = pyautogui.size()
        return [{"index": 0, "x": 0, "y": 0, "w": sw, "h": sh, "primary": True, "name": "PRIMARY"}]
    print("[INFO] Monitors (Windows virtual desktop):")
    for m in mons:
        prim = "Yes" if m["primary"] else "No "
        print(f"  MonIdx={m['index']} | Primary={prim} | x={m['x']} y={m['y']} w={m['w']} h={m['h']} | Name='{m['name']}'")
    return mons

def parse_button_spec(s: str | None):
    if not s: return None, False
    s = s.strip()
    req_mod = s[-1].lower() == 'm'
    num = s[:-1] if req_mod else s
    if num == "": return None, req_mod
    try: return int(num), req_mod
    except ValueError:
        print(f"[ERROR] Invalid button spec '{s}'. Use e.g. '25' or '25M'."); sys.exit(1)

def load_config(path: str):
    if not Path(path).exists():
        print(f"[ERROR] Config file '{path}' not found."); sys.exit(1)
    cfgp = configparser.ConfigParser(inline_comment_prefixes=(';', '#'), interpolation=None, strict=False)
    cfgp.read(path, encoding="utf-8")
    if "input" not in cfgp: print("[ERROR] Missing [input] section."); sys.exit(1)
    sec = cfgp["input"]

    def getbool(k, d): return sec.get(k, str(d)).strip().lower() in ("1","true","yes","y","on")
    def getint_or_none(k):
        v = sec.get(k, "").strip()
        return int(v) if (v and v.lstrip("+-").isdigit()) else None
    def f_or_none(k):
        s = sec.get(k, "").strip()
        if s == "": return None
        try: return float(s)
        except: print(f"[ERROR] '{k}' must be float."); sys.exit(1)

    device_guid = sec.get("device_guid", "").strip()
    device_index = getint_or_none("device_index")
    mod_guid = sec.get("modifier_device_guid", "").strip()
    mod_index = getint_or_none("modifier_device_index")
    modifier_button = getint_or_none("modifier_button")

    button_toggle_spec = sec.get("button_toggle", fallback=sec.get("button", fallback="")).strip()
    button_off_spec    = sec.get("button_off", "").strip()
    incx_spec = sec.get("button_inc_x", "").strip()
    decx_spec = sec.get("button_dec_x", "").strip()
    incy_spec = sec.get("button_inc_y", "").strip()
    decy_spec = sec.get("button_dec_y", "").strip()

    button_toggle, toggle_req_mod = parse_button_spec(button_toggle_spec)
    button_off, off_req_mod       = parse_button_spec(button_off_spec)
    incx, incx_req_mod            = parse_button_spec(incx_spec)
    decx, decx_req_mod            = parse_button_spec(decx_spec)
    incy, incy_req_mod            = parse_button_spec(incy_spec)
    decy, decy_req_mod            = parse_button_spec(decy_spec)

    if button_toggle is None:
        print("[ERROR] 'button_toggle' (or legacy 'button') is required."); sys.exit(1)

    monitor_index = sec.getint("monitor_index", fallback=0)
    x_frac, y_frac = f_or_none("x_frac"), f_or_none("y_frac")
    x_px = getint_or_none("x")
    y_px = getint_or_none("y")

    use_frac = (x_frac is not None and y_frac is not None)
    use_px   = (x_px   is not None and y_px   is not None)
    if not (use_frac or use_px):
        print("[ERROR] Provide x_frac & y_frac or x & y."); sys.exit(1)

    poll_hz = max(10, sec.getint("poll_hz", fallback=250))
    startup_grace_ms = max(0, sec.getint("startup_grace_ms", fallback=200))
    repeat_ms = max(1, sec.getint("repeat_ms", fallback=1000))
    nudge_velocity = max(1, sec.getint("nudge_velocity_px_s", fallback=600))

    clamp_space = sec.get("clamp_space", "monitor").strip().lower()
    if clamp_space not in ("monitor", "virtual"):
        clamp_space = "monitor"

    restore_on_off = getbool("restore_on_off", False)  # default: do NOT restore (per user request)

    return {
        "device_guid": device_guid, "device_index": device_index,
        "mod_guid": mod_guid, "mod_index": mod_index, "modifier_button": modifier_button,
        "button_toggle": button_toggle, "toggle_req_mod": toggle_req_mod,
        "button_off": button_off, "off_req_mod": off_req_mod,
        "incx": incx, "incx_req_mod": incx_req_mod,
        "decx": decx, "decx_req_mod": decx_req_mod,
        "incy": incy, "incy_req_mod": incy_req_mod,
        "decy": decy, "decy_req_mod": decy_req_mod,
        "nudge_velocity": nudge_velocity,
        "monitor_index": int(monitor_index),
        "x_frac": x_frac, "y_frac": y_frac, "x": x_px, "y": y_px,
        "poll_hz": poll_hz, "startup_grace_ms": startup_grace_ms, "repeat_ms": repeat_ms,
        "wiggle_one_pixel": getbool("wiggle_one_pixel", False),
        "use_sendinput": getbool("use_sendinput", platform.system().lower()=="windows"),
        "toggle_feedback": getbool("toggle_feedback", True),
        "log_apply": getbool("log_apply", False),
        "debug_buttons": getbool("debug_buttons", False),
        "clamp_space": clamp_space,
        "clamp_virtual": (clamp_space == "virtual"),
        "restore_on_off": restore_on_off,
    }

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

def event_device_id(e): return getattr(e, "instance_id", getattr(e, "joy", None))

def event_matches_device(e, js) -> bool:
    ev_id = event_device_id(e)
    try: js_id = js.get_instance_id()
    except AttributeError: js_id = js.get_id()
    return ev_id == js_id

def clamp_target(x, y, mon, clamp_virtual):
    """Clamp to selected monitor (monitor) or whole virtual desktop (virtual)."""
    if clamp_virtual:
        if platform.system().lower() == "windows":
            vx, vy, vw, vh = win_virtual_desktop_rect()
            x = max(vx, min(vx + vw - 1, x))
            y = max(vy, min(vy + vh - 1, y))
        else:
            sw, sh = pyautogui.size()
            x = max(0, min(sw - 1, x))
            y = max(0, min(sh - 1, y))
    else:
        x = max(mon["x"], min(mon["x"] + mon["w"] - 1, x))
        y = max(mon["y"], min(mon["y"] + mon["h"] - 1, y))
    return x, y

def main():
    pyautogui.FAILSAFE = False
    init_pygame()

    print("===================================================")
    print("  Joystick/Throttle → Mouse Position Repeater (modifier + hold-adjust + recenter)")
    print("===================================================\n")

    devices, inst_to_idx = list_devices(); print()
    cfg = load_config(CONFIG_FILE)

    # Monitors
    if platform.system().lower() == "windows":
        monitors = list_monitors_windows()
    else:
        sw, sh = pyautogui.size()
        monitors = [{"index": 0, "x": 0, "y": 0, "w": sw, "h": sh, "primary": True, "name": "PRIMARY"}]
        print("[INFO] Non-Windows: using primary screen only.")
    if not monitors: print("[ERROR] No monitors found."); sys.exit(1)

    mon_idx = cfg["monitor_index"]
    if not (0 <= mon_idx < len(monitors)):
        print(f"[WARN] monitor_index={mon_idx} out of range. Using 0."); mon_idx = 0
    mon = monitors[mon_idx]

    # Devices
    js = open_device_by_guid_or_index(cfg["device_guid"], cfg["device_index"])
    if js is None: print("[ERROR] Could not open primary device."); sys.exit(1)
    js_mod = None
    if cfg["modifier_button"] is not None:
        js_mod = open_device_by_guid_or_index(cfg["mod_guid"], cfg["mod_index"]) or js

    try: instance_id = js.get_instance_id()
    except AttributeError: instance_id = js.get_id()
    name = js.get_name()
    guid = js.get_guid() if hasattr(js, "get_guid") else "N/A"

    # Compute BASE target (from INI) inside selected monitor
    if cfg["x_frac"] is not None and cfg["y_frac"] is not None:
        base_x = int(round(mon["x"] + cfg["x_frac"] * mon["w"]))
        base_y = int(round(mon["y"] + cfg["y_frac"] * mon["h"]))
    else:
        base_x = int(mon["x"] + cfg["x"])
        base_y = int(mon["y"] + cfg["y"])
    base_x, base_y = clamp_target(base_x, base_y, mon, cfg["clamp_virtual"])

    # Current (mutable) target
    target_x, target_y = base_x, base_y

    print(f"[OK] Using primary device: Index={inst_to_idx.get(instance_id,'?')}, GUID={guid}, Name='{name}'")
    print(f"[OK] Using monitor: MonIdx={mon['index']} ({'Primary' if mon['primary'] else 'Secondary'}) "
          f"x={mon['x']} y={mon['y']} w={mon['w']} h={mon['h']} Name='{mon['name']}'")
    print(f"[OK] Base target from INI: ({base_x}, {base_y}) | repeat each {cfg['repeat_ms']} ms")
    print(f"[OK] Clamp space: {'virtual desktop' if cfg['clamp_virtual'] else 'selected monitor'}\n")

    # State
    pygame.event.clear()
    grace_until = time.monotonic() + (cfg["startup_grace_ms"] / 1000.0)
    repeat_interval = cfg["repeat_ms"] / 1000.0
    last_apply = time.monotonic()
    active = False
    saved_cursor = None
    wiggle_flip = False
    last_toggle = time.monotonic(); debounce_s = 0.15

    # Hold status
    hold_inc_x = hold_dec_x = hold_inc_y = hold_dec_y = False

    def modifier_is_down():
        if cfg["modifier_button"] is None: return False
        dev = js_mod or js
        try: return bool(dev.get_button(cfg["modifier_button"]))
        except Exception: return False

    def apply_cursor():
        x = target_x + (1 if (cfg["wiggle_one_pixel"] and wiggle_flip) else 0)
        y = target_y
        if cfg["use_sendinput"] and platform.system().lower() == "windows":
            sendinput_move_absolute_virtual(x, y)
        else:
            pyautogui.moveTo(x, y)

    def toggle_on():
        nonlocal active, saved_cursor, last_apply, wiggle_flip, target_x, target_y
        if active: return
        # capture cursor for optional restore
        if platform.system().lower() == "windows":
            saved_cursor = get_cursor_pos_virtual()
        else:
            pos = pyautogui.position(); saved_cursor = (int(pos[0]), int(pos[1]))
        # ALWAYS recenter to INI base when turning on
        target_x, target_y = base_x, base_y
        active = True
        if cfg["toggle_feedback"]: print("[TOGGLE] ACTIVE (recentered to INI)")
        apply_cursor(); last_apply = time.monotonic(); wiggle_flip = not wiggle_flip

    def toggle_off():
        nonlocal active, last_apply, wiggle_flip
        if not active: return
        active = False
        if cfg["toggle_feedback"]:
            print("[TOGGLE] INACTIVE" + (" (restoring)" if cfg["restore_on_off"] else ""))
        if cfg["restore_on_off"] and (saved_cursor is not None):
            x0, y0 = saved_cursor
            if cfg["use_sendinput"] and platform.system().lower() == "windows":
                sendinput_move_absolute_virtual(x0, y0)
            else:
                pyautogui.moveTo(x0, y0)
        last_apply = time.monotonic(); wiggle_flip = False

    prev_time = time.monotonic()

    try:
        while True:
            now = time.monotonic()
            dt = now - prev_time
            prev_time = now

            for event in pygame.event.get():
                # Debug prints with proper device index
                if cfg["debug_buttons"] and event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                    edge = "DOWN" if event.type == pygame.JOYBUTTONDOWN else "UP  "
                    ev_dev_id = getattr(event, "instance_id", getattr(event, "joy", None))
                    print(f"[DBG] {edge}: dev_index={inst_to_idx.get(ev_dev_id,'?')} btn={getattr(event,'button',None)} mod={'ON' if modifier_is_down() else 'off'}")

                if now < grace_until:
                    continue

                if event.type == pygame.JOYBUTTONDOWN and event_matches_device(event, js):
                    b = event.button
                    # Toggle (with optional modifier requirement)
                    if b == cfg["button_toggle"] and (not cfg["toggle_req_mod"] or modifier_is_down()):
                        if now - last_toggle >= debounce_s:
                            (toggle_off() if active else toggle_on()); last_toggle = now; continue
                    # Dedicated OFF
                    if cfg["button_off"] is not None and b == cfg["button_off"] and (not cfg["off_req_mod"] or modifier_is_down()):
                        if now - last_toggle >= debounce_s:
                            toggle_off(); last_toggle = now; continue
                    # Start HOLD-only nudges (no one-shot steps)
                    if cfg["incx"] is not None and b == cfg["incx"]:
                        hold_inc_x = True; continue
                    if cfg["decx"] is not None and b == cfg["decx"]:
                        hold_dec_x = True; continue
                    if cfg["incy"] is not None and b == cfg["incy"]:
                        hold_inc_y = True; continue
                    if cfg["decy"] is not None and b == cfg["decy"]:
                        hold_dec_y = True; continue

                elif event.type == pygame.JOYBUTTONUP and event_matches_device(event, js):
                    b = event.button
                    if cfg["incx"] is not None and b == cfg["incx"]: hold_inc_x = False
                    if cfg["decx"] is not None and b == cfg["decx"]: hold_dec_x = False
                    if cfg["incy"] is not None and b == cfg["incy"]: hold_inc_y = False
                    if cfg["decy"] is not None and b == cfg["decy"]: hold_dec_y = False

                elif event.type == pygame.JOYDEVICEREMOVED:
                    if hasattr(event, "instance_id") and event.instance_id == instance_id:
                        print("[WARN] Primary device removed. Exiting."); return

            # Apply continuous hold movement (respect 'M' requirements each tick)
            vx = (1 if (hold_inc_x and (not cfg["incx_req_mod"] or modifier_is_down())) else 0) \
               - (1 if (hold_dec_x and (not cfg["decx_req_mod"] or modifier_is_down())) else 0)
            vy = (1 if (hold_inc_y and (not cfg["incy_req_mod"] or modifier_is_down())) else 0) \
               - (1 if (hold_decy := hold_dec_y) and (not cfg["decy_req_mod"] or modifier_is_down()) else 0)

            if vx != 0 or vy != 0:
                step_x = int(round(vx * cfg["nudge_velocity"] * dt))
                step_y = int(round(vy * cfg["nudge_velocity"] * dt))
                if step_x != 0 or step_y != 0:
                    target_x += step_x; target_y += step_y
                    target_x, target_y = clamp_target(target_x, target_y, mon, cfg["clamp_virtual"])
                    if active:
                        apply_cursor(); last_apply = now; wiggle_flip = not wiggle_flip

            # Periodic re-apply while ACTIVE (keeps cursor pinned when not moving)
            if active and (now - last_apply) >= repeat_interval:
                apply_cursor(); last_apply = now; wiggle_flip = not wiggle_flip
                if cfg["log_apply"]:
                    shown_x = target_x + (1 if (cfg["wiggle_one_pixel"] and wiggle_flip) else 0)
                    print(f"[APPLY] {shown_x},{target_y} @ {now:.3f}")

            time.sleep(1.0 / float(cfg["poll_hz"]))

    except KeyboardInterrupt:
        print("\n[EXIT] User aborted.")
    finally:
        pygame.joystick.quit(); pygame.quit()

if __name__ == "__main__":
    main()

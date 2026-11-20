# -*- coding: utf-8 -*-
"""鸣潮连招助手 v6 —— 双轴连招（启动轴 + 循环轴）

主窗口：小格子显示接下来的招，严格判定（键位 + 长短按）。
运行时：先走一遍「启动轴」，走完自动进入「循环轴」并无限循环。

编辑器：点「方案」打开
  · 启动轴 / 循环轴 两条横条，用小方框显示已录入的招
  · 选 A Z E Q R 1 2 3 → 选 短按/长按 或 变奏/合轴 → 选 启动轴/循环轴 → 确认添加
"""
import ctypes
import json
import os
import queue
import shutil
import sys
import zlib
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import simpledialog, messagebox, ttk
from tkinter import font as tkfont

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # PyInstaller 单文件：__file__ 在临时解包目录，数据要放 exe 旁边
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    DEFAULT_DATA = os.path.join(APP_DIR, "方案")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    DEFAULT_DATA = APP_DIR
# 数据目录：优先环境变量，其次 exe 旁的「方案」/程序同目录
DATA_DIR = os.environ.get("COMBO_DATA_DIR") or DEFAULT_DATA
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = APP_DIR
SETTINGS = os.path.join(DATA_DIR, "settings.json")
TEAMS_FILE = os.path.join(DATA_DIR, "teams.json")

DEFAULTS = {
    "slot": 96, "gap": 12, "y_offset": 210, "alpha": 0.95, "slots": 6,
    "flash_ms": 200, "wrong_ms": 380, "hold_ms": 280, "locked": False,
}
DEFAULT_START = "A A Z E"
DEFAULT_LOOP = "1变奏 E长 Q 2合轴 R"

# ---------------- Win32 ----------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x80
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
MSG = wintypes.MSG
events = queue.Queue()
_keep, _down_mouse, _down_key = [], {}, {}

kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]


def _keyboard_proc(nCode, wParam, lParam):
    if nCode == 0:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if kb.flags & 0x10:
            return user32.CallNextHookEx(None, nCode, wParam, lParam)
        vk = int(kb.vkCode)
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            _down_key.setdefault(vk, time.time())
        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
            start = _down_key.pop(vk, None)
            dur = (time.time() - start) * 1000.0 if start else 0.0
            events.put(("key", vk, dur, None))
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def _mouse_proc(nCode, wParam, lParam):
    if nCode == 0:
        if wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            _down_mouse[wParam] = time.time()
        elif wParam in (WM_LBUTTONUP, WM_RBUTTONUP, WM_MBUTTONUP):
            base = {WM_LBUTTONUP: "LMB", WM_RBUTTONUP: "RMB", WM_MBUTTONUP: "MMB"}[wParam]
            start = _down_mouse.pop(wParam - 1, None)
            dur = (time.time() - start) * 1000.0 if start else 0.0
            ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            events.put(("mouse", base, dur, (int(ms.pt.x), int(ms.pt.y))))
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def install_hooks():
    kp, mp = HOOKPROC(_keyboard_proc), HOOKPROC(_mouse_proc)
    hmod = kernel32.GetModuleHandleW(None)
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kp, hmod, 0)
    hm = user32.SetWindowsHookExW(WH_MOUSE_LL, mp, hmod, 0)
    _keep.extend([kp, mp])
    return hk, hm


def pump():
    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def click_through(hwnd, on=True):
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
    if on:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


def force_topmost(hwnd):
    try:
        user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def enable_dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    return max(1.0, dpi / 96.0)


# ---------------- 连招解析 ----------------
IGNORE_VK = set(range(0x10, 0x13)) | {0x14, 0x5B, 0x5C, 0x09, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xFF}
ALIAS = {"A": "LMB", "左键": "LMB", "普攻": "LMB", "Z": "LMB_HOLD", "重击": "LMB_HOLD",
         "右键": "RMB", "RMB": "RMB", "中键": "MMB", "MMB": "MMB",
         "空格": "SPACE", "空格键": "SPACE", "SPACE": "SPACE"}
SKILL_NAME = {"E": "共鸣技能", "Q": "声骸技能", "R": "共鸣解放"}
LONG_MARKS = ("·长", "长按", "长", "+")
SWITCH_STATES = (("变奏", "变奏"), ("变", "变奏"), ("合奏", "合奏"), ("合轴", "合轴"), ("合", "合轴"))


def vk_label(vk):
    if vk in IGNORE_VK:
        return None
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if vk == 0x20:
        return "SPACE"
    if vk == 0x0D:
        return "ENTER"
    if vk == 0x25:
        return "LEFT"
    if vk == 0x26:
        return "UP"
    if vk == 0x27:
        return "RIGHT"
    if vk == 0x28:
        return "DOWN"
    return None


def norm_key(text):
    t = text.strip()
    if not t:
        return ""
    if t in ALIAS:
        return ALIAS[t]
    if t.upper() in ALIAS:
        return ALIAS[t.upper()]
    return t.upper()


def parse_token(tok):
    tok = tok.strip()
    if not tok:
        return None
    disp_override = None
    if "=" in tok:
        disp_override, tok = tok.split("=", 1)
    raw = tok.strip()
    hold = False
    for mark in LONG_MARKS:
        if raw.endswith(mark) and len(raw) > len(mark):
            raw, hold = raw[: -len(mark)], True
            break
    state = None
    for mark, name in SWITCH_STATES:
        if raw.endswith(mark) and len(raw) > len(mark):
            head = raw[: -len(mark)]
            if head in ("1", "2", "3"):
                raw, state = head, name
                break
    key = norm_key(raw)
    if not key:
        return None
    if hold and not key.endswith("_HOLD"):
        key += "_HOLD"
    disp = (disp_override.strip() if disp_override else raw) or raw
    if key == "SPACE" and not disp_override:
        disp = "空格"
    if state:
        label = state
    elif key in ("1", "2", "3"):
        label = "变奏"
    else:
        label = {"LMB": "普攻", "LMB_HOLD": "重击", "RMB": "右键",
                 "MMB": "中键", "SPACE": "空格"}.get(key, "")
    return disp, key, label


def parse_combo(text):
    seq = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for tok in line.split():
            tok = tok.replace("\ufeff", "").replace("\u200b", "").strip()
            item = parse_token(tok)
            if item:
                seq.append(item)
    return seq


def item_token(item):
    disp, key, label = item
    if key == "LMB":
        return "A"
    if key == "LMB_HOLD":
        return "Z"
    if key in ("E", "Q", "R", "E_HOLD", "Q_HOLD", "R_HOLD"):
        return key[0] + ("长" if key.endswith("_HOLD") else "")
    if key in ("1", "2", "3") and label in ("变奏", "合轴", "合奏"):
        return key + label
    return key


def canonical_combo(raw):
    return " ".join(item_token(i) for i in parse_combo(raw))


def slot_texts(key, label):
    if key == "LMB":
        return "普攻", "单击"
    if key == "LMB_HOLD":
        return "重击", "长按"
    if key in ("E", "Q", "R"):
        return SKILL_NAME[key], "短按"
    if key in ("E_HOLD", "Q_HOLD", "R_HOLD"):
        return SKILL_NAME[key[0]], "长按"
    if key == "SPACE":
        return None, None
    if key in ("1", "2", "3"):
        return None, label or "变奏"
    return None, label


# ---------------- 方案分享码 ----------------
# 格式：WX-XXXX-<HEX>
#   XXXX = 队伍码（CRC32 压成 4 位，防手抄错、也便于口头报）
#   HEX  = 紧凑二进制：版本 + 方案名 + 三个角色名 + 启动轴 + 循环轴
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混的 I O 0 1
CODE_MAGIC = b"WX"
CODE_VERSION = 1

TOKEN_TO_BYTE = {
    "A": 0x01, "Z": 0x02,
    "E": 0x10, "E长": 0x11, "Q": 0x12, "Q长": 0x13, "R": 0x14, "R长": 0x15,
    "1变奏": 0x20, "1合轴": 0x21, "2变奏": 0x22, "2合轴": 0x23,
    "3变奏": 0x24, "3合轴": 0x25,
    "SPACE": 0x30, "RMB": 0x31, "MMB": 0x32,
}
BYTE_TO_TOKEN = {v: k for k, v in TOKEN_TO_BYTE.items()}


def _team_code(payload):
    c = zlib.crc32(payload) & 0xFFFFFFFF
    out = []
    for _ in range(4):
        out.append(CODE_ALPHABET[c & 31])
        c >>= 5
    return "".join(out)


def _put_text(buf, text, limit=31):
    raw = (text or "").encode("utf-8")[:limit]
    buf.append(len(raw))
    buf.extend(raw)


def _get_text(data, pos):
    if pos >= len(data):
        raise ValueError("数据不完整")
    n = data[pos]
    pos += 1
    if pos + n > len(data):
        raise ValueError("数据不完整")
    return data[pos:pos + n].decode("utf-8", "replace"), pos + n


def encode_team(team):
    """把方案对象编码成分享串；失败返回 (None, 原因)。"""
    buf = bytearray(CODE_MAGIC)
    buf.append(CODE_VERSION)
    _put_text(buf, team.get("name", ""))

    names = team.get("names") or {}
    for k in ("1", "2", "3"):
        _put_text(buf, names.get(k, ""))

    for axis in ("start", "loop"):
        items = parse_combo(team.get(axis, ""))
        if len(items) > 255:
            return None, "某一轴的招太多（超过 255 个）"
        codes = []
        for item in items:
            tok = item_token(item)
            b = TOKEN_TO_BYTE.get(tok)
            if b is None:
                return None, f"这一招暂时不能分享：{tok}"
            codes.append(b)
        buf.append(len(codes))
        buf.extend(codes)

    payload = bytes(buf)
    return "WX-%s-%s" % (_team_code(payload), payload.hex().upper()), None


def decode_team(text):
    """解析分享串；返回 (team_dict, 错误信息)。"""
    s = (text or "").strip().upper().replace(" ", "").replace("\n", "")
    s = s.replace("－", "-").replace("—", "-")
    if not s.startswith("WX-"):
        return None, "开头应为 WX-"
    parts = s.split("-")
    if len(parts) != 3:
        return None, "格式应为 WX-XXXX-十六进制"
    code, hexpart = parts[1], parts[2]
    if len(code) != 4 or any(c not in CODE_ALPHABET for c in code):
        return None, "队伍码应为 4 位（不含 I O 0 1）"
    try:
        raw = bytes.fromhex(hexpart)
    except ValueError:
        return None, "十六进制部分有非法字符"

    if _team_code(raw) != code:
        return None, "队伍码对不上，可能抄错了字符"
    if raw[:2] != CODE_MAGIC:
        return None, "不是本程序生成的方案码"
    if len(raw) < 3:
        return None, "数据过短"
    ver = raw[2]
    if ver > CODE_VERSION:
        return None, f"这个方案码来自更新的版本（v{ver}），更新程序后再导入"
    if ver < 1:
        return None, "方案码版本无效"

    pos = 3
    try:
        name, pos = _get_text(raw, pos)
        names = {}
        for k in ("1", "2", "3"):
            names[k], pos = _get_text(raw, pos)
        axes = {}
        for axis in ("start", "loop"):
            n = raw[pos]
            pos += 1
            toks = []
            for _ in range(n):
                b = raw[pos]
                pos += 1
                tok = BYTE_TO_TOKEN.get(b)
                if tok is None:
                    raise ValueError(f"未知招式编码 0x{b:02X}（可能来自更新版本）")
                toks.append(tok)
            axes[axis] = " ".join(toks)
    except (IndexError, ValueError) as exc:
        return None, f"数据损坏：{exc}"

    return {"name": name or "导入方案", "names": names,
            "start": axes["start"], "loop": axes["loop"]}, None


# ---------------- 配置 / 方案 ----------------
def load_settings():
    cfg = dict(DEFAULTS)
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, "r", encoding="utf-8-sig") as fh:
                cfg.update(json.load(fh))
        except Exception:
            pass
    else:
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(DEFAULTS, fh, ensure_ascii=False, indent=2)
    return cfg


def save_settings(cfg):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_teams():
    if os.path.exists(TEAMS_FILE):
        try:
            with open(TEAMS_FILE, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if data.get("teams"):
                data["active"] = max(0, min(int(data.get("active", 0)), len(data["teams"]) - 1))
                for t in data["teams"]:
                    t.setdefault("names", {})
                    if "start" not in t and "loop" not in t:
                        t["start"] = canonical_combo(t.pop("combo", "")) or ""
                        t["loop"] = ""
                    t.setdefault("start", "")
                    t.setdefault("loop", "")
                return data
        except Exception:
            pass
    return {"active": 0, "teams": [{"name": "默认方案", "names": {},
                                    "start": DEFAULT_START, "loop": DEFAULT_LOOP}]}


def save_teams(data):
    try:
        if os.path.exists(TEAMS_FILE):
            try:
                shutil.copyfile(TEAMS_FILE, TEAMS_FILE + ".bak")
            except Exception:
                pass
        with open(TEAMS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------- UI ----------------
C_BG, C_BAR, C_TEXT = "#0e1116", "#111826", "#c9d3e0"
C_DIM, C_GREEN, C_RED, C_LABEL = "#6b7484", "#4ade80", "#f87171", "#7dd3a0"
C_PANEL, C_ACCENT, C_START, C_LOOP = "#151b25", "#2563eb", "#f59e0b", "#22d3ee"


class App:
    def __init__(self, root, cfg, scale):
        self.root, self.cfg, self.scale = root, cfg, scale
        self.slot = int(int(cfg["slot"]) * scale)
        self.gap = int(int(cfg["gap"]) * scale)
        self.n = int(cfg["slots"])
        self.alpha = float(cfg["alpha"])
        self.flash_ms, self.wrong_ms = int(cfg["flash_ms"]), int(cfg["wrong_ms"])
        self.hold_ms = int(cfg["hold_ms"])
        self.locked = bool(cfg.get("locked", False))

        self.teams = load_teams()
        self.mode = "start"
        self.start_seq, self.loop_seq = [], []
        self.seq, self.total, self.done, self.mistakes = [], 0, 0, 0
        self.paused = self.edit = False
        self.state = ""
        self.busy = False
        self.editor = None

        self._build_main()
        self._build_ctrl()
        self._place()
        self.apply_team()
        self.root.after(25, self.poll)
        self.root.after(1200, self._tick_topmost)

    def _main_size(self):
        sc = self.scale
        return (self.n * self.slot + (self.n - 1) * self.gap + int(24 * sc),
                self.slot + int(40 * sc))

    def _build_main(self):
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", self.alpha)
        r.configure(bg=C_BG)
        self.canvas = tk.Canvas(r, bg=C_BG, highlightthickness=0)
        self.canvas.pack()

    def _build_ctrl(self):
        sc = self.scale
        self.ctrl = tk.Toplevel(self.root)
        self.ctrl.overrideredirect(True)
        self.ctrl.attributes("-topmost", True)
        self.ctrl.attributes("-alpha", self.alpha)
        self.ctrl.configure(bg=C_BG)
        self.bar = tk.Frame(self.ctrl, bg=C_BG)
        self.bar.pack(fill="both", expand=True)

        fs = max(9, int(9 * sc))
        self.buttons = {}
        for text, cmd in (("方案", self.open_editor), ("重载", self.reload),
                          ("暂停", self.toggle_pause), ("重来", self.restart),
                          ("锁定", self.toggle_lock), ("退出", self.quit_app)):
            b = tk.Button(self.bar, text=text, command=cmd, bd=0, relief="flat",
                          bg=C_BAR, fg=C_TEXT, activebackground="#1f2937",
                          activeforeground="#ffffff", font=("Microsoft YaHei", fs),
                          padx=int(8 * sc), pady=int(1 * sc), cursor="hand2",
                          highlightthickness=0)
            b.pack(side="left", padx=(int(2 * sc), 0))
            b.bind("<Enter>", lambda e, w=b: w.configure(bg="#1f2937"))
            b.bind("<Leave>", lambda e, w=b: w.configure(bg=C_BAR))
            self.buttons[text] = b
        self._sync_lock_button()

        self.grip = tk.Label(self.bar, text="═ 拖动", bg=C_BAR, fg=C_DIM,
                             font=("Microsoft YaHei", max(8, int(8 * sc))),
                             cursor="fleur", padx=int(6 * sc))
        self.grip.pack(side="right", padx=(0, int(4 * sc)))
        for w in (self.grip, self.bar, self.ctrl):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)

    def _sync_lock_button(self):
        if "锁定" in self.buttons:
            self.buttons["锁定"].configure(text="已锁定" if self.locked else "锁定",
                                           fg=C_GREEN if self.locked else C_TEXT)

    def _place(self):
        sc = self.scale
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = self._main_size()
        x = self.cfg.get("x") if self.cfg.get("x") is not None else (sw - w) // 2
        y = self.cfg.get("y") if self.cfg.get("y") is not None else (
            sh - h - int(float(self.cfg["y_offset"]) * sc) - int(46 * sc))
        x, y = int(x), int(y)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.ctrl.geometry(f"{w}x{int(40 * sc)}+{x}+{y + h + int(4 * sc)}")
        self.canvas.configure(width=w, height=h)
        self._pos = (x, y)

    def _drag_start(self, ev):
        if self.locked:
            return
        self._drag_anchor = (ev.x_root, ev.y_root, self._pos[0], self._pos[1])

    def _drag_move(self, ev):
        if self.locked or not hasattr(self, "_drag_anchor"):
            return
        sx, sy, ox, oy = self._drag_anchor
        x, y = ox + (ev.x_root - sx), oy + (ev.y_root - sy)
        self._pos = (x, y)
        w, h = self._main_size()
        self.root.geometry(f"+{x}+{y}")
        self.ctrl.geometry(f"+{x}+{y + h + int(4 * self.scale)}")

    def hwnd(self):
        return user32.GetParent(self.root.winfo_id())

    def _tick_topmost(self):
        if self.locked:
            force_topmost(self.hwnd())
            try:
                force_topmost(user32.GetParent(self.ctrl.winfo_id()))
            except Exception:
                pass
        self.root.after(1200, self._tick_topmost)

    # ---------- 数据 ----------
    def current_team(self):
        return self.teams["teams"][self.teams["active"]]

    def apply_team(self):
        t = self.current_team()
        self.start_seq = parse_combo(t.get("start", ""))
        self.loop_seq = parse_combo(t.get("loop", ""))
        self.mode = "start" if self.start_seq else "loop"
        self.seq = list(self.start_seq if self.mode == "start" else self.loop_seq)
        self.total = len(self.seq)
        self.done = self.mistakes = 0
        self.state, self.busy = "", False
        self.redraw()

    # ---------- 绘制 ----------
    def build_upcoming(self):
        """永远凑满 n 格：剩余启动轴 + 循环轴（循环轴按顺序接上并无限续）。"""
        n = self.n
        items = list(self.seq)
        if self.loop_seq:
            if self.mode == "start":
                off = 0
            else:
                off = (self.total - len(self.seq)) if self.total else 0
            i = 0
            while len(items) < n:
                items.append(self.loop_seq[(off + i) % len(self.loop_seq)])
                i += 1
        return items[:n]

    def redraw(self):
        c = self.canvas
        c.delete("all")
        sc, s, g = self.scale, self.slot, self.gap
        pad, top = int(12 * sc), int(26 * sc)
        w, h = self._main_size()

        c.create_text(int(14 * sc), int(12 * sc), anchor="w",
                      text=("已暂停 · " if self.paused else "") + self.current_team()["name"],
                      fill=C_DIM, font=("Microsoft YaHei", int(8 * sc)))
        tail = f"错 {self.mistakes}" + ("  ·  已锁定" if self.locked else "")
        c.create_text(w - int(14 * sc), int(12 * sc), anchor="e", text=tail,
                      fill=C_DIM, font=("Consolas", int(9 * sc)))

        names = self.current_team().get("names") or {}
        upcoming = self.build_upcoming()
        for i in range(self.n):
            x0, y0 = pad + i * (s + g), top
            item = upcoming[i] if i < len(upcoming) else None
            if item is None:
                c.create_rectangle(x0, y0, x0 + s, y0 + s, fill="#12161d",
                                   outline="#1b2029", width=1, dash=(2, 4))
                continue
            disp, key, label = item
            if key in ("1", "2", "3"):
                nm = (names.get(key) or "").strip()
                if nm:
                    disp = nm
            cur = (i == 0)
            if cur and self.state == "ok":
                fill, outline, fg, lf = "#1f9d55", C_GREEN, "#ffffff", "#eafff2"
            elif cur and self.state == "bad":
                fill, outline, fg, lf = "#7f1d1d", C_RED, "#ffe4e6", "#ffe4e6"
            elif cur:
                fill, outline, fg, lf = "#16321f", C_GREEN, "#d1fae5", C_LABEL
            else:
                fill, outline, fg, lf = "#151a23", "#2a3240", "#9aa6b8", "#5d6878"
            c.create_rectangle(x0, y0, x0 + s, y0 + s, fill=fill, outline=outline,
                               width=2 if cur else 1)
            paint_slot_text(c, x0, y0, s, sc, disp, key, label,
                            fg, lf, bad=(cur and self.state == "bad"))

        if self.paused:
            c.create_rectangle(1, 1, w - 1, h - 1, outline="#f59e0b", width=2)

    # ---------- 输入 ----------
    def poll(self):
        try:
            while True:
                kind, val, extra, pos = events.get_nowait()
                self.handle(kind, val, extra, pos)
        except queue.Empty:
            pass
        self.root.after(25, self.poll)

    def _in_ui(self, pos):
        if not pos:
            return False
        wins = [self.root, self.ctrl]
        if self.editor and self.editor.alive:
            wins.append(self.editor.win)
        for w in wins:
            try:
                x, y = w.winfo_rootx(), w.winfo_rooty()
                if x <= pos[0] <= x + w.winfo_width() and y <= pos[1] <= y + w.winfo_height():
                    return True
            except Exception:
                pass
        return False

    def handle(self, kind, val, extra=0, pos=None):
        if self.paused or (self.editor and self.editor.alive):
            return
        if kind == "mouse":
            if self._in_ui(pos):
                return
            label = val + ("_HOLD" if extra >= self.hold_ms else "")
        else:
            base = vk_label(val)
            if not base:
                return
            label = base + ("_HOLD" if extra >= self.hold_ms else "")
        if not self.seq:
            return
        if label == self.seq[0][1]:
            self.on_correct()
        else:
            self.on_wrong()

    def on_correct(self):
        if self.busy:
            return
        self.busy, self.state = True, "ok"
        self.redraw()
        self.root.after(self.flash_ms, self._advance)

    def _advance(self):
        self.done += 1
        self.seq = self.seq[1:]
        self.state, self.busy = "", False
        if not self.seq:
            if self.mode == "start" and self.loop_seq:
                self.mode = "loop"
                self.seq = list(self.loop_seq)
                self.total = len(self.seq)
                self.done = 0
            elif self.mode == "loop" and self.loop_seq:
                self.seq = list(self.loop_seq)
                self.total = len(self.seq)
                self.done = 0
        self.redraw()

    def on_wrong(self):
        if self.busy:
            return
        self.mistakes += 1
        self.state = "bad"
        self.redraw()
        self.root.after(self.wrong_ms, self._clear_bad)

    def _clear_bad(self):
        if self.state == "bad":
            self.state = ""
            self.redraw()

    # ---------- 按钮 ----------
    def toggle_pause(self):
        self.paused = not self.paused
        self.buttons["暂停"].configure(text="继续" if self.paused else "暂停")
        self.redraw()

    def toggle_lock(self):
        self.locked = not self.locked
        self.cfg["locked"] = self.locked
        self.cfg["x"], self.cfg["y"] = self._pos
        save_settings(self.cfg)
        self._sync_lock_button()
        click_through(self.hwnd(), self.locked)
        self.grip.configure(fg=(C_DIM if self.locked else C_GREEN),
                            cursor=("arrow" if self.locked else "fleur"))
        if self.locked:
            force_topmost(self.hwnd())
            self.root.attributes("-topmost", True)
            self.ctrl.attributes("-topmost", True)
        self.redraw()

    def restart(self):
        """重来：只把进度退回启动轴开头，不读盘。"""
        self.apply_team()

    def reload(self):
        """重载：重新从 方案\\teams.json 读一遍（手改文件后用）。"""
        try:
            current_name = self.current_team().get("name")
        except Exception:
            current_name = None
        fresh = load_teams()
        idx = 0
        if current_name:
            for i, t in enumerate(fresh["teams"]):
                if t.get("name") == current_name:
                    idx = i
                    break
        fresh["active"] = idx
        self.teams = fresh
        self.apply_team()
        if self.editor and self.editor.alive:
            self.editor.refresh_teams()
            self.editor.load_axis()

    def open_editor(self):
        if self.editor and self.editor.alive:
            self.editor.win.lift()
            return
        self.editor = Editor(self)

    def quit_app(self):
        self.cfg["x"], self.cfg["y"] = self._pos
        save_settings(self.cfg)
        save_teams(self.teams)
        self.root.destroy()


def _fit_font(canvas, text, max_w, size, bold=True, min_size=6):
    """按像素宽度缩小字号，保证文字不出框。"""
    try:
        f = tkfont.Font(root=canvas, family="Microsoft YaHei", size=size,
                        weight="bold" if bold else "normal")
        while size > min_size and f.measure(text) > max_w:
            size -= 1
            f.configure(size=size)
        return f
    except Exception:
        return ("Microsoft YaHei", size, "bold" if bold else "normal")


def paint_slot_text(c, x0, y0, s, sc, disp, key, label, fg, lf, bad=False, compact=False):
    top_txt, bottom_txt = slot_texts(key, label)
    if bad:
        bottom_txt = "按错了"
    small = max(6, int(0.085 * s))
    if compact:
        big = int(0.30 * s)
        f_big = _fit_font(c, disp, s * 0.84, big)
        c.create_text(x0 + s / 2, y0 + s * 0.36, text=disp, fill=fg, font=f_big)
        if bottom_txt:
            f_small = _fit_font(c, bottom_txt, s * 0.92, max(6, int(0.070 * s)), bold=False)
            c.create_text(x0 + s / 2, y0 + s * 0.79, text=bottom_txt, fill=lf, font=f_small)
        return
    if top_txt:
        f_top = _fit_font(c, top_txt, s * 0.90, small, bold=False)
        c.create_text(x0 + s / 2, y0 + s * 0.17, text=top_txt, fill=lf, font=f_top)
    big = int(0.27 * s)
    f_big = _fit_font(c, disp, s * 0.82, big)
    c.create_text(x0 + s / 2, y0 + s * (0.46 if top_txt else 0.44), text=disp,
                  fill=fg, font=f_big)
    if bottom_txt:
        f_small = _fit_font(c, bottom_txt, s * 0.92, small, bold=False)
        c.create_text(x0 + s / 2, y0 + s * 0.80, text=bottom_txt, fill=lf, font=f_small)


# ---------------- 编辑器 ----------------
class Editor:
    KEYS = ["A", "Z", "E", "Q", "R", "1", "2", "3", "SPACE"]

    def __init__(self, app):
        self.app = app
        self.alive = True
        sc = app.scale
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)          # 去掉系统白色标题栏
        self.win.configure(bg=C_BG)
        ww, wh = int(720 * sc), int(600 * sc)   # 含自绘标题栏
        sx = max(0, (self.win.winfo_screenwidth() - ww) // 2)
        sy = max(0, (self.win.winfo_screenheight() - wh) // 2)
        self.win.geometry(f"{ww}x{wh}+{sx}+{sy}")
        self.ww, self.wh, self.wx, self.wy = ww, wh, sx, sy
        self._resize_job = None
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        fs = max(9, int(9 * sc))

        # 自绘标题栏（可拖动）
        head = tk.Frame(self.win, bg=C_PANEL)
        head.pack(fill="x")
        htitle = tk.Label(head, text="连招方案", bg=C_PANEL, fg=C_TEXT,
                          font=("Microsoft YaHei", int(9 * sc), "bold"))
        htitle.pack(side="left", padx=int(10 * sc), pady=int(4 * sc))
        hclose = tk.Button(head, text="✕", command=self.close, bd=0, bg=C_PANEL,
                           fg=C_DIM, activebackground="#7f1d1d", activeforeground="#fff",
                           font=("Microsoft YaHei", int(10 * sc)), padx=int(10 * sc),
                           cursor="hand2", highlightthickness=0)
        hclose.pack(side="right")
        for w in (head, htitle):
            w.bind("<Button-1>", self._win_drag_start)
            w.bind("<B1-Motion>", self._win_drag_move)
        self.axes = {"start": [], "loop": []}
        self.axis_var = tk.StringVar(value="启动轴")

        # 方案行
        top = tk.Frame(self.win, bg=C_BG)
        top.pack(fill="x", padx=int(10 * sc), pady=(int(8 * sc), int(4 * sc)))
        tk.Label(top, text="方案", bg=C_BG, fg=C_DIM,
                 font=("Microsoft YaHei", fs)).pack(side="left")

        self.name_btn = tk.Frame(top, bg=C_PANEL, cursor="hand2",
                                 highlightthickness=1, highlightbackground="#2a3240")
        self.name_label = tk.Label(self.name_btn, text="", bg=C_PANEL, fg=C_TEXT,
                                   font=("Microsoft YaHei", fs, "bold"),
                                   padx=int(10 * sc), pady=int(3 * sc))
        self.name_label.pack(side="left")
        self.name_arrow = tk.Label(self.name_btn, text="▾", bg=C_PANEL, fg=C_DIM,
                                   font=("Microsoft YaHei", fs), padx=int(8 * sc))
        self.name_arrow.pack(side="left")
        self.name_btn.pack(side="left", padx=(int(6 * sc), int(10 * sc)))
        for w in (self.name_btn, self.name_label, self.name_arrow):
            w.bind("<Button-1>", self.open_team_menu)
            w.bind("<Enter>", lambda e: self._hover_name(True))
            w.bind("<Leave>", lambda e: self._hover_name(False))

        def action_btn(text, cmd):
            b = tk.Button(top, text=text, command=cmd, bd=0, relief="flat",
                          bg=C_BAR, fg=C_TEXT, activebackground=C_ACCENT,
                          activeforeground="#ffffff", font=("Microsoft YaHei", fs),
                          padx=int(9 * sc), pady=int(2 * sc), cursor="hand2",
                          highlightthickness=0)
            b.pack(side="left", padx=(0, int(4 * sc)))
            b.bind("<Enter>", lambda e, w=b: w.configure(bg="#1f2937"))
            b.bind("<Leave>", lambda e, w=b: w.configure(bg=C_BAR))
            return b

        action_btn("新建", self.new_team)
        action_btn("改名", self.rename_team)
        action_btn("删除", self.del_team)
        action_btn("分享", self.share_team)
        action_btn("导入", self.import_team)

        # 角色名 2x4
        grid = tk.Frame(self.win, bg=C_BG)
        grid.pack(fill="x", padx=int(10 * sc), pady=(0, int(6 * sc)))
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="team")
        grid.columnconfigure(3, weight=0)
        self.name_entries = {}
        for c, idx in enumerate(("1", "2", "3")):
            tk.Label(grid, text=idx, bg=C_PANEL, fg=C_DIM,
                     font=("Consolas", int(10 * sc), "bold")).grid(
                row=0, column=c, sticky="nsew", padx=1, pady=(0, 1))
            e = tk.Entry(grid, bg="#0b0f16", fg=C_TEXT, relief="flat",
                         insertbackground=C_TEXT, justify="center",
                         font=("Microsoft YaHei", fs))
            e.grid(row=1, column=c, sticky="nsew", padx=1)
            e.insert(0, self.team().get("names", {}).get(idx, ""))
            e.bind("<FocusIn>", lambda ev: self.enter_name_edit())
            e.bind("<Return>", lambda ev: self.save_names())
            self.name_entries[idx] = e
        tk.Label(grid, text="", bg=C_BG).grid(row=0, column=3)
        self.save_names_btn = tk.Button(
            grid, text="保存", bd=0, bg=C_ACCENT, fg="#fff",
            activebackground="#1d4ed8", activeforeground="#fff",
            font=("Microsoft YaHei", fs), padx=int(10 * sc), cursor="hand2",
            command=self.save_names)
        self.save_names_btn.grid(row=1, column=3, sticky="nsew", padx=(int(4 * sc), 0))

        # 两条轴
        self.axis_canvas = {}
        for axis, title, color in (("start", "启动轴", C_START), ("loop", "循环轴", C_LOOP)):
            row = tk.Frame(self.win, bg=C_BG)
            row.pack(fill="x", padx=int(10 * sc), pady=(0, int(4 * sc)))
            tk.Label(row, text=title, bg=C_BG, fg=color, width=5, anchor="n",
                     font=("Microsoft YaHei", fs, "bold")).pack(
                side="left", anchor="n", pady=(int(6 * sc), 0))
            cv = tk.Canvas(row, bg="#0b0f16", height=int(100 * sc),
                           highlightthickness=0)
            cv.pack(side="left", fill="x", expand=True)
            cv.bind("<Configure>", self._on_canvas_resize)
            cv.bind("<MouseWheel>", lambda e, c=cv: c.yview_scroll(-1 if e.delta > 0 else 1, "units"))
            self.axis_canvas[axis] = cv

        # 轴选择 + 键盘
        keyarea = tk.Frame(self.win, bg=C_BG)
        keyarea.pack(fill="x", padx=int(10 * sc), pady=(int(4 * sc), 0))
        for opt in ("启动轴", "循环轴"):
            tk.Radiobutton(keyarea, text=opt, value=opt, variable=self.axis_var,
                           indicatoron=0, bd=0, bg=C_BAR, fg=C_TEXT,
                           selectcolor=C_ACCENT, activebackground="#1f2937",
                           font=("Microsoft YaHei", fs), padx=int(8 * sc),
                           cursor="hand2").pack(side="left", padx=(0, int(4 * sc)))
        tk.Frame(keyarea, bg=C_BG, width=int(10 * sc)).pack(side="left")

        keys = tk.Frame(self.win, bg=C_BG)
        keys.pack(fill="x", padx=int(10 * sc), pady=(int(6 * sc), 0))
        self.sel_key = None
        self.key_btns = {}
        for k in self.KEYS:
            label = "空格" if k == "SPACE" else k
            b = tk.Button(keys, text=label, width=(4 if k == "SPACE" else 3), bd=0,
                          bg=C_BAR, fg=C_TEXT,
                          activebackground="#1f2937", activeforeground="#fff",
                          font=("Microsoft YaHei", int(12 * sc), "bold"),
                          cursor="hand2", command=lambda kk=k: self.pick_key(kk))
            b.pack(side="left", padx=(0, int(3 * sc)))
            self.key_btns[k] = b

        # 修饰
        self.mod_frame = tk.Frame(self.win, bg=C_BG)
        self.mod_frame.pack(fill="x", padx=int(10 * sc), pady=(int(6 * sc), 0))
        self.mod_hint = tk.Label(self.mod_frame,
                                 text="选 E/Q/R 再选 短按或长按 ；选 1/2/3 再选 变奏或合轴",
                                 bg=C_BG, fg=C_DIM, font=("Microsoft YaHei", fs))
        self.mod_hint.pack(side="left")
        self.mod_var = tk.StringVar(value="")
        self.mod_btns = {}
        for opt in ("短按", "长按", "变奏", "合轴"):
            b = tk.Radiobutton(self.mod_frame, text=opt, value=opt, variable=self.mod_var,
                               indicatoron=0, bd=0, bg=C_BAR, fg=C_TEXT,
                               selectcolor="#1d4ed8", activebackground="#1f2937",
                               font=("Microsoft YaHei", fs), padx=int(10 * sc),
                               cursor="hand2", command=self.update_preview)
            self.mod_btns[opt] = b

        # 底部
        bottom = tk.Frame(self.win, bg=C_BG)
        bottom.pack(fill="x", padx=int(10 * sc), pady=(int(8 * sc), int(10 * sc)))
        self.preview = tk.Label(bottom, text="先选一个键", bg=C_BG, fg=C_DIM,
                                font=("Microsoft YaHei", int(10 * sc)))
        self.preview.pack(side="left")
        for text, cmd in (("确认添加", self.add_move), ("撤销", self.undo_move),
                          ("清空", self.clear_axis), ("关闭", self.close)):
            tk.Button(bottom, text=text, command=cmd, bd=0,
                      bg=(C_ACCENT if text == "确认添加" else C_BAR),
                      fg="#fff" if text == "确认添加" else C_TEXT,
                      activebackground="#1d4ed8", activeforeground="#fff",
                      font=("Microsoft YaHei", fs), padx=int(10 * sc),
                      cursor="hand2").pack(side="right", padx=(int(4 * sc), 0))

        # 保存并关闭（确认添加下方，整行）
        saverow = tk.Frame(self.win, bg=C_BG)
        saverow.pack(fill="x", padx=int(10 * sc), pady=(0, int(10 * sc)))
        tk.Button(saverow, text="保存并关闭", command=self.save_and_close, bd=0,
                  bg="#166534", fg="#ffffff", activebackground="#15803d",
                  activeforeground="#ffffff", font=("Microsoft YaHei", fs, "bold"),
                  pady=int(3 * sc), cursor="hand2").pack(fill="x")

        self.refresh_teams()
        self.load_axis()
        self.update_preview()

    # --- 窗口拖动 ---
    def _win_drag_start(self, ev):
        self._win_anchor = (ev.x_root, ev.y_root, self.wx, self.wy)

    def _win_drag_move(self, ev):
        if not hasattr(self, "_win_anchor"):
            return
        sx, sy, ox, oy = self._win_anchor
        self.wx = ox + (ev.x_root - sx)
        self.wy = oy + (ev.y_root - sy)
        self.win.geometry(f"+{self.wx}+{self.wy}")

    # --- 方案 ---
    def team(self):
        return self.app.teams["teams"][self.app.teams["active"]]

    def refresh_teams(self):
        t = self.team()
        self.name_label.configure(text=t["name"])
        # 名字长时缩字号
        fs = max(9, int(9 * self.app.scale))
        if len(t["name"]) > 8:
            self.name_label.configure(font=("Microsoft YaHei", max(8, fs - 1), "bold"))

    def _hover_name(self, on):
        bg = "#1f2937" if on else C_PANEL
        for w in (self.name_btn, self.name_label, self.name_arrow):
            w.configure(bg=bg)
        self.name_btn.configure(highlightbackground=C_ACCENT if on else "#2a3240")

    def open_team_menu(self, ev=None):
        sc = self.app.scale
        m = tk.Menu(self.win, tearoff=0, bg=C_PANEL, fg=C_TEXT,
                    activebackground=C_ACCENT, activeforeground="#ffffff",
                    bd=0, relief="flat", font=("Microsoft YaHei", max(9, int(9 * sc))))
        active = self.app.teams["active"]
        for i, t in enumerate(self.app.teams["teams"]):
            mark = "● " if i == active else "    "
            m.add_command(label=mark + t["name"],
                          command=lambda i=i: self.select_team(i))
        try:
            m.tk_popup(self.name_btn.winfo_rootx(),
                       self.name_btn.winfo_rooty() + self.name_btn.winfo_height())
        finally:
            m.grab_release()

    def select_team(self, idx):
        if idx == self.app.teams["active"]:
            return
        self.commit()
        self.app.teams["active"] = idx
        save_teams(self.app.teams)
        self.refresh_teams()
        self.app.apply_team()
        self.load_axis()

    def commit(self):
        t = self.team()
        t["names"] = {k: e.get().strip() for k, e in self.name_entries.items()}
        t["start"] = " ".join(item_token(i) for i in self.axes["start"])
        t["loop"] = " ".join(item_token(i) for i in self.axes["loop"])
        save_teams(self.app.teams)

    def load_axis(self):
        t = self.team()
        self.axes["start"] = parse_combo(t.get("start", ""))
        self.axes["loop"] = parse_combo(t.get("loop", ""))
        for k, e in self.name_entries.items():
            e.delete(0, "end")
            e.insert(0, t.get("names", {}).get(k, ""))
        self.enter_name_edit()
        self.refresh_axes()

    def _on_canvas_resize(self, ev):
        if self._resize_job:
            try:
                self.win.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.win.after(80, self.refresh_axes)

    def refresh_axes(self):
        """固定窗口大小：画布高度不变，招多时自动换行并在框内滚动。"""
        self._resize_job = None
        sc = self.app.scale
        size, gap, pad = int(40 * sc), int(6 * sc), int(4 * sc)
        rowh = size + gap
        for axis, cv in self.axis_canvas.items():
            wpx = cv.winfo_width()
            if wpx <= 1:
                wpx = self.ww - int(90 * sc)
            per = max(1, (wpx - 2 * pad + gap) // (size + gap))
            items = self.axes[axis]
            cv.delete("all")
            if not items:
                cv.create_text(pad + int(4 * sc), rowh / 2 + pad, anchor="w",
                               text="（空）", fill=C_DIM,
                               font=("Microsoft YaHei", max(7, int(8 * sc))))
                cv.configure(scrollregion=(0, 0, wpx, rowh + pad))
                cv.yview_moveto(0)
                continue
            rows = (len(items) + per - 1) // per
            for idx, item in enumerate(items):
                r, c = divmod(idx, per)
                x = pad + c * (size + gap)
                y = pad + r * rowh
                disp, key, label = item
                if key in ("1", "2", "3"):
                    nm = (self.team().get("names") or {}).get(key, "").strip()
                    if nm:
                        disp = nm[0]  # 小方框只显示首字，避免出圈
                cv.create_rectangle(x, y, x + size, y + size, fill="#151a23",
                                    outline="#2a3240")
                paint_slot_text(cv, x, y, size, sc, disp, key, label,
                                "#c9d3e0", "#7a8598", compact=True)
            cv.configure(scrollregion=(0, 0, wpx, pad + rows * rowh + pad))

    def new_team(self):
        name = simpledialog.askstring("新建方案", "方案名称：", parent=self.win)
        if not name:
            return
        self.commit()
        self.app.teams["teams"].append({"name": name.strip(), "names": {}, "start": "", "loop": ""})
        self.app.teams["active"] = len(self.app.teams["teams"]) - 1
        save_teams(self.app.teams)
        self.refresh_teams()
        self.app.apply_team()
        self.axes = {"start": [], "loop": []}
        self.refresh_axes()

    def rename_team(self):
        old = self.team()["name"]
        name = simpledialog.askstring("重命名", "新名称：", initialvalue=old, parent=self.win)
        if not name:
            return
        self.team()["name"] = name.strip()
        self.commit()
        self.refresh_teams()
        self.app.redraw()

    def del_team(self):
        if len(self.app.teams["teams"]) <= 1:
            messagebox.showinfo("提示", "至少保留一个方案", parent=self.win)
            return
        if not messagebox.askyesno("删除", f"删除「{self.team()['name']}」？", parent=self.win):
            return
        self.app.teams["teams"].pop(self.app.teams["active"])
        self.app.teams["active"] = 0
        save_teams(self.app.teams)
        self.refresh_teams()
        self.app.apply_team()
        self.load_axis()

    # --- 名字 ---
    def enter_name_edit(self):
        try:
            self.save_names_btn.configure(text="保存", bg=C_ACCENT)
        except Exception:
            pass

    def save_names(self):
        self.commit()
        try:
            self.win.focus_set()
            for e in self.name_entries.values():
                e.selection_clear()
            self.save_names_btn.configure(text="已保存", bg="#166534")
        except Exception:
            pass
        self.refresh_axes()
        self.app.redraw()

    # --- 连招 ---
    def pick_key(self, k):
        self.sel_key = k
        for kk, b in self.key_btns.items():
            b.configure(bg=(C_ACCENT if kk == k else C_BAR))
        self.mod_hint.pack_forget()
        for opt, b in self.mod_btns.items():
            b.pack_forget()
        if k in ("E", "Q", "R"):
            self.mod_var.set("短按")
            self.mod_btns["短按"].pack(side="left", padx=(0, int(4 * self.app.scale)))
            self.mod_btns["长按"].pack(side="left")
        elif k in ("1", "2", "3"):
            self.mod_var.set("变奏")
            self.mod_btns["变奏"].pack(side="left", padx=(0, int(4 * self.app.scale)))
            self.mod_btns["合轴"].pack(side="left")
        else:
            self.mod_var.set("")
        self.update_preview()

    def build_token(self):
        k = self.sel_key
        if k == "A":
            return "A"
        if k == "Z":
            return "Z"
        if k == "SPACE":
            return "SPACE"
        if k in ("E", "Q", "R"):
            return k + ("长" if self.mod_var.get() == "长按" else "")
        if k in ("1", "2", "3"):
            return k + (self.mod_var.get() or "变奏")
        return None

    def update_preview(self):
        k = self.sel_key
        if not k:
            self.preview.configure(text="先选一个键", fg=C_DIM)
            return
        tok = self.build_token()
        item = parse_token(tok)
        if not item:
            self.preview.configure(text="先选一个键", fg=C_RED)
            return
        top, bottom = slot_texts(item[1], item[2])
        txt = f"将载入【{self.axis_var.get()}】：{item[0]}"
        if top:
            txt += f"  {top}"
        if bottom:
            txt += f" {bottom}"
        self.preview.configure(text=txt, fg=C_GREEN)

    def add_move(self):
        tok = self.build_token()
        if not tok:
            self.preview.configure(text="先选一个键", fg=C_RED)
            return
        axis = "start" if self.axis_var.get() == "启动轴" else "loop"
        self.axes[axis].append(parse_token(tok))
        self.commit()
        self.refresh_axes()
        self.app.apply_team()

    def undo_move(self):
        axis = "start" if self.axis_var.get() == "启动轴" else "loop"
        if not self.axes[axis]:
            return
        self.axes[axis].pop()
        self.commit()
        self.refresh_axes()
        self.app.apply_team()

    def clear_axis(self):
        axis = "start" if self.axis_var.get() == "启动轴" else "loop"
        self.axes[axis] = []
        self.commit()
        self.refresh_axes()
        self.app.apply_team()

    # --- 分享 / 导入 ---
    def share_team(self):
        self.commit()
        code, err = encode_team(self.team())
        if err:
            messagebox.showerror("分享失败", err, parent=self.win)
            return
        dlg = self._dialog("分享方案", int(560 * self.app.scale), int(240 * self.app.scale))
        tk.Label(dlg, text="把这个字符串发给别人，对方点「导入」粘贴即可：",
                 bg=C_BG, fg=C_DIM, font=("Microsoft YaHei", 9)).pack(
            anchor="w", padx=10, pady=(10, 4))
        box = tk.Text(dlg, height=5, wrap="char", bg="#0b0f16", fg=C_TEXT,
                      bd=0, insertbackground=C_TEXT, font=("Consolas", 9))
        box.pack(fill="both", expand=True, padx=10)
        box.insert("1.0", code)
        box.configure(state="disabled")

        row = tk.Frame(dlg, bg=C_BG)
        row.pack(fill="x", padx=10, pady=8)

        def do_copy():
            self.win.clipboard_clear()
            self.win.clipboard_append(code)
            btn.configure(text="已复制")

        btn = tk.Button(row, text="复制到剪贴板", command=do_copy, bd=0, bg=C_ACCENT,
                        fg="#fff", font=("Microsoft YaHei", 9), padx=12, cursor="hand2")
        btn.pack(side="left")
        tk.Button(row, text="关闭", command=dlg.destroy, bd=0, bg=C_BAR, fg=C_TEXT,
                  font=("Microsoft YaHei", 9), padx=12, cursor="hand2").pack(side="right")

    def import_team(self):
        dlg = self._dialog("导入方案", int(560 * self.app.scale), int(240 * self.app.scale))
        tk.Label(dlg, text="粘贴对方发来的方案码（WX-XXXX-…）：",
                 bg=C_BG, fg=C_DIM, font=("Microsoft YaHei", 9)).pack(
            anchor="w", padx=10, pady=(10, 4))
        box = tk.Text(dlg, height=5, wrap="char", bg="#0b0f16", fg=C_TEXT,
                      bd=0, insertbackground=C_TEXT, font=("Consolas", 9))
        box.pack(fill="both", expand=True, padx=10)
        box.focus_set()

        row = tk.Frame(dlg, bg=C_BG)
        row.pack(fill="x", padx=10, pady=8)

        def do_import():
            team, err = decode_team(box.get("1.0", "end"))
            if err:
                messagebox.showerror("导入失败", err, parent=dlg)
                return
            names = [t["name"] for t in self.app.teams["teams"]]
            base = team["name"]
            new_name = base
            i = 2
            while new_name in names:
                new_name = f"{base}({i})"
                i += 1
            team["name"] = new_name
            self.commit()
            self.app.teams["teams"].append(team)
            self.app.teams["active"] = len(self.app.teams["teams"]) - 1
            save_teams(self.app.teams)
            self.refresh_teams()
            self.app.apply_team()
            self.load_axis()
            dlg.destroy()

        tk.Button(row, text="导入", command=do_import, bd=0, bg=C_ACCENT, fg="#fff",
                  font=("Microsoft YaHei", 9), padx=12, cursor="hand2").pack(side="left")
        tk.Button(row, text="取消", command=dlg.destroy, bd=0, bg=C_BAR, fg=C_TEXT,
                  font=("Microsoft YaHei", 9), padx=12, cursor="hand2").pack(side="right")

    def _dialog(self, title, w, h):
        sc = self.app.scale
        dlg = tk.Toplevel(self.win)
        dlg.overrideredirect(True)
        dlg.configure(bg=C_BG)
        x = self.wx + (self.ww - w) // 2
        y = self.wy + (self.wh - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        dlg.attributes("-topmost", True)
        head = tk.Frame(dlg, bg=C_PANEL)
        head.pack(fill="x")
        tk.Label(head, text=title, bg=C_PANEL, fg=C_TEXT,
                 font=("Microsoft YaHei", int(9 * sc), "bold")).pack(
            side="left", padx=int(8 * sc), pady=int(3 * sc))
        tk.Button(head, text="✕", command=dlg.destroy, bd=0, bg=C_PANEL, fg=C_DIM,
                  font=("Microsoft YaHei", int(9 * sc)), padx=int(8 * sc),
                  cursor="hand2").pack(side="right")
        return dlg

    def save_and_close(self):
        self.commit()
        save_teams(self.app.teams)
        self.app.apply_team()
        self.close()

    def close(self):
        self.commit()
        self.alive = False
        self.win.destroy()


def main():
    if "--selftest" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        hk, hm = install_hooks()
        print("keyboard:", "OK" if hk else "FAIL", "mouse:", "OK" if hm else "FAIL")
        d = load_teams()
        print("teams:", [(t["name"], t["start"], t["loop"]) for t in d["teams"]])
        root.destroy()
        return 0

    cfg = load_settings()
    scale = enable_dpi()
    root = tk.Tk()
    app = App(root, cfg, scale)
    threading.Thread(target=pump, daemon=True).start()
    install_hooks()
    root.after(300, lambda: click_through(app.hwnd(), app.locked))
    if os.environ.get("COMBO_OPEN_EDITOR"):
        root.after(600, app.open_editor)
    root.protocol("WM_DELETE_WINDOW", app.quit_app)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

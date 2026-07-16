"""Pet window — cross-platform via PySide6.

Works on macOS, Windows, and Linux. Uses Qt's frameless + translucent
window flags, which behave correctly on all three platforms (whereas
Tk's transparency is unreliable on macOS and PyObjC's NSWindow is macOS-only).
"""

import os
import sys
import time
import shutil
import subprocess
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QPoint, QSize
from PySide6.QtGui import QPixmap, QPainter, QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QWidget, QMenu
import requests
import cairosvg

from .bot_svg import make_svg, make_svg_cached, ANIM_CYCLE

SIZE = 160
TICK_MS = 110
SERVER_URL = "http://localhost:5050/state"


def _resolve_sounds():
    bundled = os.path.join(os.path.dirname(__file__), "assets")
    fallback = {
        "success":   os.path.join(bundled, "success.wav"),
        "error":     os.path.join(bundled, "error.wav"),
        "attention": os.path.join(bundled, "attention.wav"),
    }
    if sys.platform == "darwin":
        candidates = {
            "success":   "/System/Library/Sounds/Glass.aiff",
            "error":     "/System/Library/Sounds/Basso.aiff",
            "attention": "/System/Library/Sounds/Morse.aiff",
        }
    elif sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        media = os.path.join(windir, "Media")
        candidates = {
            "success":   os.path.join(media, "Windows Notify System Generic.wav"),
            "error":     os.path.join(media, "Windows Critical Stop.wav"),
            "attention": os.path.join(media, "Windows Notify.wav"),
        }
    else:
        base = "/usr/share/sounds/freedesktop/stereo"
        candidates = {
            "success":   os.path.join(base, "complete.oga"),
            "error":     os.path.join(base, "dialog-error.oga"),
            "attention": os.path.join(base, "message.oga"),
        }
    return {k: (candidates[k] if os.path.exists(candidates[k]) else fallback[k])
            for k in candidates}


def _play_audio(path):
    if not path or not os.path.exists(path):
        return None
    if sys.platform == "darwin":
        if shutil.which("afplay"):
            return subprocess.Popen(["afplay", path],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
    elif sys.platform == "win32":
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass
        return None
    else:
        for cmd in (
            ["paplay", path],
            ["aplay", "-q", path],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            ["mpg123", "-q", path],
        ):
            if shutil.which(cmd[0]):
                return subprocess.Popen(cmd,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
    return None


class SoundPlayer:
    PATTERNS = {
        "success":   {"count": 3, "interval": 0.22},
        "attention": {"count": 2, "interval": 0.30},
        "error":     {"count": 1, "interval": 0.00},
    }

    def __init__(self):
        self.sounds = _resolve_sounds()
        self._procs = []
        self._lock = threading.Lock()

    def play(self, key):
        path = self.sounds.get(key)
        if not path:
            return
        cfg = self.PATTERNS.get(key, {"count": 1, "interval": 0.0})
        with self._lock:
            for p in self._procs:
                if p and p.poll() is None:
                    try:
                        p.terminate()
                    except Exception:
                        pass
            self._procs.clear()

        def play_one():
            proc = _play_audio(path)
            if proc:
                with self._lock:
                    self._procs.append(proc)

        play_one()
        for i in range(1, cfg["count"]):
            t = threading.Timer(i * cfg["interval"], play_one)
            t.daemon = True
            t.start()


def _always_on_top_flags():
    """Flags that keep the pet visible above every app on every OS."""
    return (
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.SplashScreen
        | Qt.NoDropShadowWindowHint
    )


class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(_always_on_top_flags())
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(SIZE, SIZE)
        self._move_to_bottom_right()

        self.current_status = "idle"
        self.frame_idx = 0
        self.last_state_change = time.time()
        self.pixmap = None
        self.drag_pos = None
        self.drag_start = None
        self.drag_distance = 0
        self.sound = SoundPlayer()
        self.menu = self._build_menu()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        threading.Thread(target=self._poll_loop, daemon=True).start()

    def showEvent(self, event):
        super().showEvent(event)
        self._pin_to_top_macos()

    def _pin_to_top_macos(self):
        if sys.platform != "darwin":
            return
        try:
            from AppKit import (
                NSApp,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorStationary,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
            )
            NS_STATUS_WINDOW_LEVEL = 25
            for w in NSApp().windows():
                try:
                    w.setLevel_(NS_STATUS_WINDOW_LEVEL)
                    w.setCollectionBehavior_(
                        NSWindowCollectionBehaviorCanJoinAllSpaces
                        | NSWindowCollectionBehaviorStationary
                        | NSWindowCollectionBehaviorFullScreenAuxiliary
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _move_to_bottom_right(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        margin = 24
        self.move(
            screen.right() - SIZE - margin,
            screen.bottom() - SIZE - margin,
        )

    def _build_menu(self):
        m = QMenu(self)
        for label, target in [
            ("Hello", lambda: self.poke("curious")),
            ("Working", lambda: self.poke("working")),
            ("Celebrate", lambda: self.poke("proud")),
            ("Sleep", lambda: self._set_state("sleeping", post=True)),
            (None, None),
            ("Reset position", self._move_to_bottom_right),
            (None, None),
            ("Quit", QApplication.instance().quit),
        ]:
            if label is None:
                m.addSeparator()
                continue
            act = QAction(label, self)
            act.triggered.connect(target)
            m.addAction(act)
        return m

    def _tick(self):
        try:
            if self.current_status == "idle":
                elapsed = time.time() - self.last_state_change
                show_clock = (elapsed % 30) < 4 and elapsed > 6
                if show_clock:
                    time_str = datetime.now().strftime("%H:%M")
                    svg = make_svg(
                        "idle", self.frame_idx % ANIM_CYCLE,
                        time_str=time_str, override_eye="clock",
                    )
                else:
                    svg = make_svg_cached("idle", self.frame_idx % ANIM_CYCLE)
            else:
                svg = make_svg_cached(self.current_status, self.frame_idx % ANIM_CYCLE)
            png = cairosvg.svg2png(
                bytestring=svg.encode("utf-8"),
                output_width=SIZE, output_height=SIZE,
            )
            pix = QPixmap()
            pix.loadFromData(png)
            self.pixmap = pix
            self.update()
        except Exception as e:
            print(f"[pet] render error: {e}")
        self.frame_idx += 1

    def paintEvent(self, event):
        if self.pixmap is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(0, 0, self.pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            gp = event.globalPosition().toPoint()
            self.drag_pos = gp - self.frameGeometry().topLeft()
            self.drag_start = gp
            self.drag_distance = 0
            event.accept()
        elif event.button() == Qt.RightButton:
            self.menu.exec(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self.drag_pos is not None:
            gp = event.globalPosition().toPoint()
            self.move(gp - self.drag_pos)
            if self.drag_start is not None:
                self.drag_distance = max(
                    self.drag_distance, (gp - self.drag_start).manhattanLength()
                )
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.drag_distance < 4:
                self.poke("curious")
            self.drag_pos = None
            self.drag_start = None

    def _poll_loop(self):
        while True:
            try:
                r = requests.get(SERVER_URL, timeout=1)
                if r.ok:
                    data = r.json()
                    new = data.get("status", "idle")
                    event = (data.get("last_event") or "").lower()
                    if new != self.current_status:
                        self._set_state(new, post=False)
                        if event in ("notification", "userpromptsubmit"):
                            self.sound.play("attention")
            except Exception:
                pass
            if (
                self.current_status == "idle"
                and time.time() - self.last_state_change > 180
            ):
                self._set_state("sleeping", post=False)
            time.sleep(0.4)

    def _set_state(self, new, post=True, auto_revert=None):
        if new == self.current_status:
            return
        self.current_status = new
        self.frame_idx = 0
        self.last_state_change = time.time()
        if auto_revert is not None:
            self._schedule_revert(auto_revert)
        elif new == "success":
            self.sound.play("success")
            self._schedule_revert(3.0)
        elif new == "error":
            self.sound.play("error")
            self._schedule_revert(3.5)
        elif new in ("proud", "curious"):
            self._schedule_revert(4.0)
        if post:
            try:
                requests.post(SERVER_URL, json={"status": new, "event": "ui"},
                              timeout=0.5)
            except Exception:
                pass

    def _schedule_revert(self, seconds):
        def revert():
            self.current_status = "idle"
            self.frame_idx = 0
            self.last_state_change = time.time()
            try:
                requests.post(
                    SERVER_URL,
                    json={"status": "idle", "event": "auto-revert"},
                    timeout=0.5,
                )
            except Exception:
                pass
        t = threading.Timer(seconds, revert)
        t.daemon = True
        t.start()

    def poke(self, state):
        self._set_state(state, post=True, auto_revert=2.5)


def _hide_from_dock_on_macos():
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        pass


def _show_in_dock_on_macos():
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(0)
    except Exception:
        pass


def _load_app_icon() -> QIcon:
    here = os.path.dirname(__file__)
    assets = os.path.join(here, "assets")
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512, 1024):
        path = os.path.join(assets, f"icon_{size}.png")
        if os.path.exists(path):
            icon.addFile(path, QSize(size, size))
    return icon


def main(show_in_dock: bool = False):
    if show_in_dock:
        _show_in_dock_on_macos()
    else:
        _hide_from_dock_on_macos()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("Claude Pet")
    app.setApplicationDisplayName("Claude Pet")
    app.setWindowIcon(_load_app_icon())
    pet = PetWindow()
    pet.setWindowIcon(_load_app_icon())
    pet.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""Pet window — cross-platform via PySide6.

Works on macOS, Windows, and Linux. Uses Qt's frameless + translucent
window flags, which behave correctly on all three platforms (whereas
Tk's transparency is unreliable on macOS and PyObjC's NSWindow is macOS-only).
"""

import json
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
from . import memory

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
        self._active_break = None
        self._break_snooze_until = 0.0
        self._break_pending_since = None    # set when a prompt was deferred

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._github_watch_loop, daemon=True).start()

    def _github_watch_loop(self):
        """Background poll of watched GitHub repos. Sleeps 30s between wakeups
        and only actually hits the network when a repo is due per its interval.
        Failures never bubble up — the pet must never crash on watcher errors."""
        while True:
            try:
                from .github_watch import watcher as gh_watcher
                gh_watcher.poll_all_due()
            except Exception as exc:
                print(f"[pet] github watcher error: {exc}")
            time.sleep(30)

    def _drain_github_alerts(self):
        """Deliver at most one pending GitHub event as a pet reaction per tick.

        Multiple events queue up naturally — each subsequent tick emits the
        next one, so a burst of activity plays through instead of stacking.
        """
        try:
            from .github_watch import storage as gh_storage
            pending = gh_storage.pending_alerts()
            if not pending:
                return
            ev = pending[0]
            reaction = ev.get("reaction", "curious")
            # Reaction → pet state + sound. States that already exist in bot_svg.
            state_map = {"success": "success", "error": "error",
                         "curious": "curious"}
            self._set_state(state_map.get(reaction, "curious"), post=True)
            sound_map = {"success": "success", "error": "error",
                         "curious": "attention"}
            self.sound.play(sound_map.get(reaction, "attention"))
            gh_storage.mark_alerted(ev["id"])
        except Exception as exc:
            print(f"[pet] github alert delivery error: {exc}")

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
        # Rebuilt fresh every popup so the Ergonomics toggle label reflects
        # the current on/off state.
        m = QMenu(self)
        from .ergonomics import config as ergo_cfg
        ergo_enabled = ergo_cfg.load().get("enabled", True)
        for label, target in [
            ("Hello", lambda: self.poke("curious")),
            ("Working", lambda: self.poke("working")),
            ("Celebrate", lambda: self.poke("proud")),
            ("Sleep", lambda: self._set_state("sleeping", post=True)),
            (None, None),
            ("Take a break now", self._trigger_break_now),
            ("Snooze breaks 30 min", lambda: self._snooze_breaks(30 * 60)),
            (f"Turn Ergonomics {'OFF' if ergo_enabled else 'ON'}",
             self._toggle_ergonomics),
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

    def _toggle_ergonomics(self):
        from .ergonomics import config as ergo_cfg
        cfg = ergo_cfg.load()
        cfg["enabled"] = not cfg.get("enabled", True)
        ergo_cfg.save(cfg)
        # Rebuild menu next time it opens so the label refreshes.

    def _trigger_break_now(self):
        """Manual break trigger — picks the most overdue category or eyes as default."""
        from .ergonomics import scheduler, exercises, tracker
        # Find whichever category has the biggest overrun; fall back to eyes.
        best_cat = "eyes"
        best_score = -1.0
        for cat in ("eyes", "neck", "wrists", "posture", "hydration"):
            elapsed = tracker.active_seconds_since_last(cat)
            if elapsed > best_score:
                best_score = elapsed
                best_cat = cat
        exercise = exercises.for_category(best_cat)
        if exercise:
            self._show_break(exercise.category, exercise.slug)

    def _snooze_breaks(self, seconds: float):
        from .ergonomics import scheduler
        self._break_snooze_until = scheduler.snooze_until(seconds)

    def _show_break(self, category: str, exercise_slug: str):
        from .ergonomics.overlay import BreakOverlay
        from .ergonomics import tracker
        # Distinct chime for break prompts (Purr is soft, non-alarming).
        self.sound.play("attention")
        def on_close(completed: bool):
            tracker.note_break_completed(category, exercise_slug, completed)
        self._active_break = BreakOverlay(exercise_slug, on_close)
        self._active_break.show()

    def _tick(self):
        try:
            # Look up top tier once per ~5 seconds (~45 frames) to keep DB pressure low.
            if self.frame_idx % 45 == 0:
                try:
                    self._tier = memory.top_tier()
                except Exception:
                    self._tier = None
            tier = getattr(self, "_tier", None)
            if self.current_status == "idle":
                elapsed = time.time() - self.last_state_change
                show_clock = (elapsed % 30) < 4 and elapsed > 6
                if show_clock:
                    time_str = datetime.now().strftime("%H:%M")
                    svg = make_svg(
                        "idle", self.frame_idx % ANIM_CYCLE,
                        time_str=time_str, override_eye="clock", tier=tier,
                    )
                else:
                    svg = make_svg("idle", self.frame_idx % ANIM_CYCLE, tier=tier)
            else:
                svg = make_svg(self.current_status, self.frame_idx % ANIM_CYCLE, tier=tier)
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

        # Ergonomics scheduler check — cheap enough to run every tick, and
        # break thresholds are minutes so we never over-prompt. Only when
        # there's no already-open break overlay.
        if (self._active_break is None or not self._active_break.isVisible()) \
                and (self.frame_idx % 30 == 0):
            self._maybe_prompt_break()
            self._drain_break_queue()

        # GitHub alert delivery — cheap DB read every ~3s; only fires the pet
        # reaction when a new event is genuinely pending.
        if self.frame_idx % 30 == 0:
            self._drain_github_alerts()

    def _drain_break_queue(self):
        """Check /break for a CLI/menu-triggered request and open the overlay."""
        try:
            import urllib.request as _u
            with _u.urlopen("http://localhost:5050/break", timeout=0.3) as r:
                pending = json.loads(r.read())
            if not pending.get("queued"):
                return
            # Ack immediately so we don't reopen on the next tick.
            _u.urlopen(_u.Request("http://localhost:5050/break/ack",
                                  data=b'{}', method="POST",
                                  headers={"Content-Type": "application/json"}),
                       timeout=0.3).read()
            slug = pending.get("slug")
            category = pending.get("category")
            if slug:
                from .ergonomics import exercises as _ex
                ex = _ex.get(slug)
                if ex:
                    self._show_break(ex.category, ex.slug)
                    return
            if category:
                from .ergonomics import exercises as _ex
                ex = _ex.for_category(category)
                if ex:
                    self._show_break(ex.category, ex.slug)
                    return
            # No specifier → pick the most-overdue.
            self._trigger_break_now()
        except Exception:
            pass    # transient — try again next tick

    def _maybe_prompt_break(self):
        try:
            from .ergonomics import scheduler, config as ergo_config, tracker as ergo_tracker
            cfg = ergo_config.load()
            if not cfg.get("enabled", True):
                return
            if ergo_config.is_quiet_hours(cfg):
                return
            if time.time() < self._break_snooze_until:
                return
            prompt = scheduler.check_due(
                pet_status=self.current_status,
                last_activity_at=self.last_state_change,
                thresholds=ergo_config.effective_thresholds(cfg),
                pending_since=self._break_pending_since,
            )
            if prompt is None:
                # If we were pending and no longer due (state changed), clear it.
                self._break_pending_since = None
                return
            # If the scheduler said 'due but I want to defer' vs 'due, prompt now':
            # our check_due only returns non-None when it's OK to prompt. If it
            # ever returns None while we've been sitting on a pending, we clear.
            self._break_pending_since = None
            self._show_break(prompt.category, prompt.exercise_slug)
        except Exception as e:
            print(f"[pet] ergonomics check error: {e}")

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
            # Rebuild the menu each time so the Ergonomics ON/OFF label
            # reflects the current toggle state.
            self.menu = self._build_menu()
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
                # Short click: open the memory panel + a curious reaction.
                self.poke("curious")
                self._toggle_panel()
            self.drag_pos = None
            self.drag_start = None

    def _toggle_panel(self):
        """Open or close the memory panel. Lazy import so headless installs
        (no display) never need to load the panel module."""
        panel = getattr(self, "_panel", None)
        if panel is not None and panel.isVisible():
            panel.close()
            return
        try:
            from .panel import MemoryPanel
            if panel is None:
                panel = MemoryPanel(self)
                self._panel = panel
            panel._refresh_all()
            panel.show()
            panel.raise_()
        except Exception as e:
            print(f"[pet] panel unavailable: {e}")

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
        # Ergonomics: activity vs idle transitions gate the tracker.
        from .ergonomics import tracker as _ergo_tracker
        if new == "sleeping":
            _ergo_tracker.mark_idle()
        elif self.current_status == "sleeping":
            _ergo_tracker.mark_activity()
        # Any real work state also counts as activity — cheap idempotent write.
        if new in ("reading", "writing", "running", "working", "thinking"):
            _ergo_tracker.mark_activity()
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


def _set_macos_dock_icon():
    """Override the Python-launcher icon in the Dock / Cmd-Tab / Force-Quit
    with our mascot. Uses NSApp.setApplicationIconImage_ via PyObjC."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSImage
        assets = os.path.join(os.path.dirname(__file__), "assets")
        # Prefer the biggest PNG so the Dock's ~128px slot renders sharp.
        for name in ("icon_1024.png", "icon_512.png", "icon_256.png"):
            path = os.path.join(assets, name)
            if os.path.exists(path):
                img = NSImage.alloc().initWithContentsOfFile_(path)
                if img is not None:
                    NSApplication.sharedApplication().setApplicationIconImage_(img)
                return
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
    # Override the Python launcher icon in Dock / Cmd-Tab / Force-Quit
    _set_macos_dock_icon()
    pet = PetWindow()
    pet.setWindowIcon(_load_app_icon())
    pet.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

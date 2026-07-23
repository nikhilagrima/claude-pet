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
    # Single beep per event. Was 3 for success, 2 for attention — combined
    # with Claude Code's rapid-fire hook events during active work, the
    # 3-beep bursts stacked back-to-back and read as "continuous beeping"
    # to the user. Ergonomics: a single crisp chirp per event carries the
    # same signal without the machine-gun feel.
    PATTERNS = {
        "success":   {"count": 1, "interval": 0.00},
        "attention": {"count": 1, "interval": 0.00},
        "error":     {"count": 1, "interval": 0.00},
    }

    # Per-key cooldown — same sound key can't fire twice within this window.
    # Kills the "keep beeping" experience when Claude Code emits a burst of
    # Stop / UserPromptSubmit events during active work (each was chirping
    # even after count=1). Error stays at 0 so failures are never suppressed.
    COOLDOWN_S = {
        "success":   5.0,
        "attention": 5.0,
        "error":     0.0,
    }

    def __init__(self):
        self.sounds = _resolve_sounds()
        self._procs = []
        self._lock = threading.Lock()
        self._last_played_at: dict[str, float] = {}

    def play(self, key):
        # Global mute — checked fresh every call so a UI toggle or `claude-pet
        # mute` takes effect immediately, no restart. Cost: one file read
        # per sound event (~microseconds).
        try:
            from . import pet_config
            if pet_config.is_muted():
                return
        except Exception:
            pass    # never let the mute check itself block sound
        # Per-key cooldown — silently drop repeat plays inside the window.
        cooldown = self.COOLDOWN_S.get(key, 0.0)
        if cooldown > 0:
            now = time.time()
            last = self._last_played_at.get(key, 0.0)
            if now - last < cooldown:
                return
            self._last_played_at[key] = now
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
    """Flags that keep the pet visible above every app on every OS.

    Design notes on the flag combo (macOS is picky):
    - Qt.SplashScreen fades + hides on app deactivate. Not used.
    - Qt.Tool auto-hides when owning app loses focus on macOS. Not used.
    - Qt.WindowDoesNotAcceptFocus was tried; over-blocks — better to rely
      on the macOS-native hidesOnDeactivate=NO in _pin_to_top_macos.
    On Linux/Windows, FramelessWindowHint + WindowStaysOnTopHint is
    sufficient. On macOS the heavy lifting is done by _pin_to_top_macos
    which sets NSWindow.level = 1500 (assistiveTechHighWindow — highest
    level not reserved by the system) plus a 1-second watchdog that
    re-asserts everything when Mission Control / fullscreen transitions
    demote us.
    """
    return (
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
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
            except Exception:
                from .errors import log_exception
                log_exception("app._github_watch_loop")
            time.sleep(30)

    def _drain_github_alerts(self):
        """Deliver at most one pending GitHub event per tick — sound + pet
        emotion + visible toast so the developer knows WHAT happened, not
        just that something happened.

        Multiple events queue naturally: one per tick keeps them from
        stacking as a wall of toasts.
        """
        try:
            from .github_watch import storage as gh_storage
            pending = gh_storage.pending_alerts()
            if not pending:
                return
            ev = pending[0]
            reaction = ev.get("reaction", "curious")
            # Pet emotion — states that already exist in bot_svg.
            state_map = {"success": "success", "error": "error",
                         "curious": "curious"}
            self._set_state(state_map.get(reaction, "curious"), post=True)
            # Distinct sound per reaction.
            sound_map = {"success": "success", "error": "error",
                         "curious": "attention"}
            self.sound.play(sound_map.get(reaction, "attention"))
            # Toast — shows the summary + link. Kept as a lazy import so
            # headless installs don't need the notification module loaded.
            try:
                from .github_watch.notification import GithubActivityToast
                slug = f"{ev.get('owner', '?')}/{ev.get('repo', '?')}"
                toast = GithubActivityToast(
                    title=ev.get("title") or "GitHub activity",
                    repo_slug=slug,
                    reaction=reaction,
                    url=ev.get("url"),
                )
                # Keep a strong ref so Python doesn't GC it while it's shown.
                self._active_gh_toasts = getattr(self, "_active_gh_toasts", [])
                # Drop dismissed ones lazily so the list doesn't grow forever.
                self._active_gh_toasts = [
                    t for t in self._active_gh_toasts if t.isVisible()
                ]
                self._active_gh_toasts.append(toast)
                toast.show()
            except Exception:
                from .errors import log_exception
                log_exception("app._drain_github_alerts.toast")
            gh_storage.mark_alerted(ev["id"])
        except Exception:
            from .errors import log_exception
            log_exception("app._drain_github_alerts")

    def showEvent(self, event):
        super().showEvent(event)
        self._pin_to_top()

    def _pin_to_top(self):
        """Cross-platform 'always on top' with a per-OS strategy.

        macOS: NSWindow.level=1500 + accessory activation policy + Space
               observer. Qt.WindowStaysOnTopHint alone is not enough.
        Windows: Qt.WindowStaysOnTopHint gets us most of the way but
                 focus events can demote us; a periodic SetWindowPos
                 HWND_TOPMOST reasserts it.
        Linux X11: Qt.WindowStaysOnTopHint maps to _NET_WM_STATE_ABOVE
                   which most WMs honor. No extra work needed unless the
                   user sets a WM that ignores it.
        Linux Wayland: compositor controls stacking by design. Best-
                       effort with WindowStaysOnTopHint.
        """
        if sys.platform == "darwin":
            self._pin_to_top_macos()
        elif sys.platform == "win32":
            self._pin_to_top_windows()
        # Linux: nothing extra beyond the Qt flag; watchdog is still
        # installed for consistency and future WM-specific tweaks.
        if not getattr(self, "_pin_watchdog", None):
            self._pin_watchdog = QTimer(self)
            self._pin_watchdog.timeout.connect(self._watchdog_tick)
            # 250ms on macOS to catch fullscreen transitions; 750ms
            # everywhere else is enough since demotions are rarer.
            interval = 250 if sys.platform == "darwin" else 750
            self._pin_watchdog.start(interval)

    def _watchdog_tick(self):
        """Called at 4Hz on macOS, ~1.3Hz elsewhere. Cheap fast path when
        the pin is already at target."""
        if sys.platform == "darwin":
            self._apply_macos_pin(force=False)
        elif sys.platform == "win32":
            self._pin_to_top_windows()

    def _pin_to_top_windows(self):
        """SetWindowPos(HWND_TOPMOST) via ctypes.

        Qt.WindowStaysOnTopHint takes care of the initial pinning but
        Windows will drop us out of the topmost set on focus loss in
        some scenarios (esp. when a fullscreen app has just closed).
        Reasserting HWND_TOPMOST every watchdog tick is the standard
        workaround.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010     # crucial: don't steal focus
            SWP_ASYNCWINDOWPOS = 0x4000  # non-blocking
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        except Exception:
            pass

    def _pin_to_top_macos(self):
        """Aggressively keep the pet above every other window on macOS.

        macOS silently lowers a window's level after Mission Control, Space
        switches, fullscreen transitions, or when other apps grab focus. A
        one-shot pin at startup isn't enough — the pet reappears buried
        behind other apps within minutes.

        Countermeasures stacked:
        - Level = NSScreenSaverWindowLevel (1000). Higher than status bar
          (25), popup menus (101), or floating panels (3). Only the menu
          bar sits above it in normal use.
        - CollectionBehavior includes CanJoinAllSpaces (every desktop),
          Stationary (doesn't slide with Space switches),
          FullScreenAuxiliary (shows over fullscreen apps),
          IgnoresCycle (Cmd-Tab skips it → no focus theft that could hide).
        - hidesOnDeactivate = NO. Prevents auto-hide when the app is not
          frontmost — the root cause of the "goes away when I open another
          app" complaint.
        - orderFrontRegardless: brings to front WITHOUT stealing focus
          from whatever the user is actively using.
        - A repeating 2s QTimer re-asserts all of the above so nothing —
          not fullscreen video, not screensaver return, not desktop
          switching — can quietly demote us.
        """
        self._apply_macos_pin(force=True)
        # Register for NSWorkspace notifications so we re-pin the INSTANT
        # macOS activates a different Space (e.g. another app entered
        # fullscreen). Without this, the pet is invisible on the new Space
        # until the next watchdog tick.
        self._install_space_change_observer()

    # macOS window level — assistiveTechHighWindow (1500) is the highest
    # level user-space apps get without system entitlements. Higher than
    # NSScreenSaverWindowLevel (1000) and NSPopUpMenuWindowLevel (101).
    # Reference: CGWindowLevelForKey(kCGAssistiveTechHighWindowLevelKey).
    _MACOS_TARGET_LEVEL = 1500

    def _apply_macos_pin(self, *, force: bool):
        """Assert level + collection behavior on every NSWindow the app owns.

        Called on show, from the watchdog, and from `changeEvent` when the
        app activation state flips. `force=True` re-applies unconditionally;
        `force=False` (watchdog path) only rewrites when the level dropped
        below target — keeps the tick cheap when nothing's wrong.
        """
        if sys.platform != "darwin":
            return
        try:
            from AppKit import (
                NSApp,
                NSApplication,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorStationary,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
            )
            # Re-assert accessory activation policy — the specific setting
            # macOS demands before it will render our window above other
            # apps' fullscreen mode. Without this, level=1500 and
            # canJoinAllSpaces are BOTH ignored on fullscreen Spaces.
            ns_app = NSApplication.sharedApplication()
            if ns_app.activationPolicy() != 1:
                ns_app.setActivationPolicy_(1)
            behavior = (
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorIgnoresCycle
            )
            target = self._MACOS_TARGET_LEVEL
            for w in NSApp().windows():
                try:
                    # Skip windows the caller already hid — otherwise the pin
                    # resurrects dismissed BreakOverlays as empty grey squares
                    # above the mascot (level=1500 + orderFrontRegardless
                    # bypasses close()).
                    if not w.isVisible():
                        continue
                    if force or w.level() < target:
                        w.setLevel_(target)
                        w.setCollectionBehavior_(behavior)
                        w.setHidesOnDeactivate_(False)
                        w.orderFrontRegardless()
                except Exception:
                    pass
        except Exception:
            pass

    def _install_space_change_observer(self):
        """Register a PyObjC observer for NSWorkspaceActiveSpaceDidChangeNotification.

        Without this, when the user (or another app) switches to a new Space
        or enters fullscreen, the pet stays on the ORIGINAL Space until the
        watchdog next fires. The user sees the pet vanish. The observer
        fires the very moment the Space activates and we re-pin instantly.
        """
        if sys.platform != "darwin":
            return
        if getattr(self, "_space_observer", None) is not None:
            return
        try:
            from AppKit import NSWorkspace
            from Foundation import NSObject

            # Capture `self` (the PetWindow) for use inside the callback.
            pet = self

            class _SpaceObserver(NSObject):
                # PyObjC signature: void (id) — accepts one NSNotification arg.
                def onSpaceChange_(self_, _notification):
                    try:
                        pet._apply_macos_pin(force=True)
                    except Exception:
                        pass

            observer = _SpaceObserver.new()
            NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
                observer,
                b"onSpaceChange:",
                "NSWorkspaceActiveSpaceDidChangeNotification",
                None,
            )
            # Keep strong refs so Python doesn't GC them and cancel the reg.
            self._space_observer = observer
        except Exception as exc:
            print(f"[pet] space observer install failed: {exc}")

    def changeEvent(self, event):
        """Re-pin whenever activation state flips.

        macOS reliably drops the window level on ActivationChange, so this
        handler catches the demotion within a single event loop tick —
        faster than waiting for the 1-second watchdog to notice.
        """
        try:
            from PySide6.QtCore import QEvent
            if event.type() in (
                QEvent.ActivationChange, QEvent.WindowStateChange,
                QEvent.ApplicationActivate, QEvent.ApplicationDeactivate,
            ):
                self._apply_macos_pin(force=True)
        except Exception:
            pass
        super().changeEvent(event)

    def _move_to_bottom_right(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        margin = 24
        self.move(
            screen.right() - SIZE - margin,
            screen.bottom() - SIZE - margin,
        )

    def _build_menu(self):
        # Rebuilt fresh every popup so the Ergonomics + Sound toggle labels
        # reflect the current on/off state.
        m = QMenu(self)
        from .ergonomics import config as ergo_cfg
        from . import pet_config
        ergo_enabled = ergo_cfg.load().get("enabled", True)
        muted = pet_config.is_muted()
        for label, target in [
            ("Hello", lambda: self.poke("curious")),
            ("Working", lambda: self.poke("working")),
            ("Celebrate", lambda: self.poke("proud")),
            ("Sleep", lambda: self._set_state("sleeping", post=True)),
            (None, None),
            (f"{'Unmute sound' if muted else 'Mute sound'}",
             self._toggle_mute),
            (None, None),
            ("Take a break now", self._trigger_break_now),
            ("Snooze breaks 30 min", lambda: self._snooze_breaks(30 * 60)),
            (f"Turn Ergonomics {'OFF' if ergo_enabled else 'ON'}",
             self._toggle_ergonomics),
            (None, None),
            ("Reset position", self._move_to_bottom_right),
            ("Hide pet  (unhide with:  claude-pet show)",
             lambda: self.hide()),
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

    def _toggle_mute(self):
        """Flip the pet-wide mute flag. Sound player checks the config on
        every play() so this takes effect on the next event, no restart.

        Feedback strategy:
        - Muting: silent — the pet does a curious blink so the click is
          visible but no sound plays (that's the whole point).
        - Unmuting: play a success chime AFTER flipping the flag so the
          user hears immediate confirmation that audio is back.
        """
        from . import pet_config
        new_state = pet_config.toggle_muted()
        if new_state:
            # Just went silent — visual ack only.
            self.poke("curious")
        else:
            # Just came back — audible ack.
            self.poke("success")
            self.sound.play("success")

    def _toggle_ergonomics(self):
        """Flip the ergonomics coach enabled/disabled state. Menu label is
        rebuilt on the next right-click so it reflects the new state."""
        from .ergonomics import config as ergo_cfg
        cfg = ergo_cfg.load()
        cfg["enabled"] = not cfg.get("enabled", True)
        ergo_cfg.save(cfg)
        # If we just disabled ergonomics AND a break overlay is on-screen,
        # dismiss it immediately — otherwise it stays open forever with no
        # way to close (scheduler won't tick, and user has already said no).
        if not cfg["enabled"] and self._active_break is not None:
            try:
                if self._active_break.isVisible():
                    self._active_break._finish(completed=False)
            except Exception:
                pass
            self._active_break = None

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

        # Visibility queue — same 3s cadence. `claude-pet hide/show` POSTs a
        # desired state to /visibility; we drain here and call hide()/show()
        # on the QWidget. That's how the CLI unhides the pet after it's been
        # hidden (right-click menu is unreachable when the window is gone).
        if self.frame_idx % 30 == 0:
            self._drain_visibility_queue()

        # Fitness scheduler — same cadence. Fires at most one reminder per
        # tick (per day); scheduler.check_due returns "workout" | "weigh_in"
        # | "meal_check" | None. Kept behind a config-enabled guard so the
        # module is invisible until the user turns it on.
        if self.frame_idx % 30 == 0:
            self._drain_fitness_scheduler()

        # Personal reminders — same cadence. Fires bubbles for any reminder
        # whose day-before / 5-min-before / on-time stage has come due and
        # hasn't been shown yet. At most one bubble per tick to avoid a
        # wall of pop-ups.
        if self.frame_idx % 30 == 0:
            self._drain_reminders()

    def _drain_reminders(self):
        """Fire at most one reminder bubble per tick."""
        try:
            from .reminders import scheduler as rsched, store as rstore
            from .reminders.overlay import ReminderBubble
            due = rsched.check_due()
            if not due:
                return
            first = due[0]
            r = first.reminder
            stage = first.stage
            # Mark stage fired immediately so we don't re-pop next tick
            rstore.mark_stage_fired(r["id"], stage)
            bubble = ReminderBubble(
                reminder=r,
                stage_label=rsched.label_for_stage(stage),
                on_done=lambda rid=r["id"]: rstore.mark_completed(rid),
                on_snooze=lambda mins, rid=r["id"]: rstore.snooze(rid, mins),
                on_dismiss=lambda: None,
            )
            self._active_reminder_bubbles = getattr(
                self, "_active_reminder_bubbles", []
            )
            self._active_reminder_bubbles = [
                b for b in self._active_reminder_bubbles if b.isVisible()
            ]
            self._active_reminder_bubbles.append(bubble)
            bubble.show()
        except Exception:
            from .errors import log_exception
            log_exception("app._drain_reminders")

    def _drain_fitness_scheduler(self):
        """Show at most one fitness bubble per tick — workout / weigh-in /
        meal-check based on time-of-day. Also displays the weekly coaching
        note if Claude Code wrote one since last shown."""
        try:
            from .fitness import scheduler as fsched
            from .fitness import config as fcfg
            from .fitness import coach as fcoach
            from .fitness import tracker as ftrack
            from .fitness import overlay as foverlay
            from .fitness import plan as fplan

            # First check: is a coaching note waiting to be shown once?
            if fcoach.note_needs_showing():
                note = fcoach.latest_note()
                if note:
                    bubble = foverlay.CoachNoteBubble(
                        note, on_close=fcoach.mark_note_shown,
                    )
                    self._active_fitness_bubbles = getattr(
                        self, "_active_fitness_bubbles", []
                    )
                    self._active_fitness_bubbles = [
                        b for b in self._active_fitness_bubbles if b.isVisible()
                    ]
                    self._active_fitness_bubbles.append(bubble)
                    bubble.show()
                    return       # don't stack a reminder on top

            # Then the scheduled reminders
            due = fsched.check_due()
            if not due:
                return

            self._active_fitness_bubbles = getattr(
                self, "_active_fitness_bubbles", []
            )
            self._active_fitness_bubbles = [
                b for b in self._active_fitness_bubbles if b.isVisible()
            ]

            if due == "workout":
                bubble = foverlay.WorkoutBubble(
                    fcoach.daily_nudge(),
                    on_close=lambda: fsched.mark_fired("workout"),
                )
            elif due == "weigh_in":
                current = ftrack.latest_weight() or float(
                    fcfg.profile().get("weight_kg", 80)
                )
                bubble = foverlay.WeighInBubble(
                    current_kg=current,
                    on_submit=lambda kg: (
                        ftrack.log_weight(kg),
                        fsched.mark_fired("weigh_in"),
                    ),
                    on_dismiss=lambda: fsched.mark_fired("weigh_in"),
                )
            elif due == "meal_check":
                bubble = foverlay.MealCheckBubble(
                    on_submit=lambda on_plan, note: (
                        ftrack.log_meal(on_plan, note),
                        fsched.mark_fired("meal_check"),
                    ),
                    on_dismiss=lambda: fsched.mark_fired("meal_check"),
                )
            else:
                return

            self._active_fitness_bubbles.append(bubble)
            bubble.show()
        except Exception:
            from .errors import log_exception
            log_exception("app._drain_fitness_scheduler")

    def _drain_visibility_queue(self):
        """Check /visibility for a CLI-triggered show/hide and apply it."""
        try:
            import urllib.request as _u
            with _u.urlopen("http://localhost:5050/visibility", timeout=0.3) as r:
                pending = json.loads(r.read())
            if not pending.get("queued"):
                return
            # Ack immediately so we don't reapply next tick.
            _u.urlopen(_u.Request("http://localhost:5050/visibility/ack",
                                  data=b'{}', method="POST",
                                  headers={"Content-Type": "application/json"}),
                       timeout=0.3).read()
            want_visible = bool(pending.get("show"))
            if want_visible and not self.isVisible():
                self.show()
                # Re-pin so it comes back at level 1500 + accessory + on
                # all Spaces, not just wherever macOS decides to show it.
                self._apply_macos_pin(force=True)
            elif not want_visible and self.isVisible():
                self.hide()
        except Exception:
            pass

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
    app = QApplication(sys.argv)
    # CRITICAL — activation policy MUST be set AFTER QApplication is
    # constructed. Qt's initialization re-creates NSApplication and resets
    # any earlier setActivationPolicy_ call. Without accessory mode (policy
    # = 1), macOS refuses to render the window above other apps' fullscreen
    # mode — even with level=1500 and canJoinAllSpaces. Confirmed via
    # Electron docs: `app.dock.hide()` is required for fullscreen override.
    if show_in_dock:
        _show_in_dock_on_macos()
    else:
        _hide_from_dock_on_macos()
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

import os
import subprocess
import threading
import time
from logging import getLogger

import constants
import globalVars

try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    HAS_PYCAW = True
except Exception:
    AudioUtilities = None
    ISimpleAudioVolume = None
    HAS_PYCAW = False


def getDeviceList():
    """アクティブな再生デバイス一覧を返す。戻り値: [{id, name}]"""
    if not HAS_PYCAW:
        return []

    devices = []
    try:
        for d in AudioUtilities.GetAllDevices():
            dev_id = str(getattr(d, "id", ""))
            state = str(getattr(d, "state", ""))
            name = getattr(d, "FriendlyName", "")
            if dev_id.startswith("{0.0.0.") and "Active" in state and name:
                devices.append({"id": dev_id, "name": name})
    except Exception:
        return []

    unique = []
    seen = set()
    for d in devices:
        key = (d["id"], d["name"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    return unique


class MPVAudioPlayer:
    def __init__(self):
        self._log = getLogger(f"{constants.LOG_PREFIX}.MPVAudioPlayer")
        self._source = None
        self._http_headers = {}
        self._volume = 100
        self._device_id = ""
        self._process = None
        self._last_error = ""
        self._lock = threading.RLock()
        self._mpv_path = constants.MPV_PATH
        self._load_device_from_config()

    def _load_device_from_config(self):
        try:
            cfg = globalVars.app.config
            if cfg.has_section("livePlay"):
                self._device_id = cfg.getstring("livePlay", "device_id", "")
            else:
                self._device_id = ""
        except Exception:
            self._device_id = ""

    def _save_device_to_config(self):
        try:
            cfg = globalVars.app.config
            if not cfg.has_section("livePlay"):
                cfg["livePlay"] = {}
            cfg["livePlay"]["device_id"] = self._device_id
            cfg.write()
        except Exception:
            pass

    def setSource(self, source):
        with self._lock:
            self._source = source

    def setHttpHeaders(self, headers):
        with self._lock:
            self._http_headers = dict(headers or {})

    def setVolume(self, value):
        with self._lock:
            self._volume = max(0, min(100, int(value)))
            if self._is_running_locked():
                if not self._apply_runtime_volume_locked():
                    self._restart_locked()

    def setDeviceByName(self, device_id):
        with self._lock:
            requested = device_id or ""
            if requested and not self._is_active_device_id(requested):
                self._log.warning("Selected output device is not active: %s", requested)
                self._device_id = ""
                self._save_device_to_config()
                if self._is_running_locked():
                    self._restart_locked()
                return False

            self._device_id = requested
            self._save_device_to_config()
            if self._is_running_locked():
                self._restart_locked()
            return True

    def play(self):
        with self._lock:
            if not self._source:
                return
            self._start_locked()

    def stop(self):
        with self._lock:
            self._stop_locked()

    def exit(self):
        self.stop()

    def isPlaying(self):
        with self._lock:
            return self._is_running_locked()

    def getLastError(self):
        with self._lock:
            return self._last_error

    def _is_active_device_id(self, device_id):
        for d in getDeviceList():
            if d["id"] == device_id:
                return True
        return False

    def _build_command(self):
        cmd = [
            self._mpv_path,
            "--no-video",
            "--force-window=no",
            "--no-terminal",
            "--keep-open=no",
            "--really-quiet",
            "--network-timeout=15",
            "--cache=yes",
            "--cache-secs=20",
            "--demuxer-readahead-secs=20",
            "--stream-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=2,http_seekable=0,seekable=0",
            self._source,
        ]
        if self._http_headers:
            header_str = ",".join([f"{k}: {v}" for k, v in self._http_headers.items() if v])
            if header_str:
                cmd.insert(-1, f"--http-header-fields={header_str}")
        if self._device_id:
            cmd.insert(-1, f"--audio-device=wasapi/{self._device_id}")
        return cmd

    def _start_locked(self):
        self._stop_locked()
        self._last_error = ""

        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self._log.debug("Starting mpv command: %s", " ".join(self._build_command()))
            self._process = subprocess.Popen(
                self._build_command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except Exception as e:
            self._last_error = str(e)
            self._process = None
            self._log.error("Failed to start mpv: %s", e)
            return

        # 起動直後に終了した場合の理由を保持
        time.sleep(0.6)
        if self._process and self._process.poll() is not None:
            return_code = self._process.returncode
            stderr = self._read_process_stderr_locked()
            if stderr.strip():
                self._last_error = stderr.strip()
            else:
                self._last_error = f"mpv exited immediately (returncode={return_code})"
            self._log.error("mpv exited immediately: %s", self._last_error)
            self._process = None
            return

        if self._device_id:
            time.sleep(0.8)
            if self._process.poll() is not None:
                stderr = self._read_process_stderr_locked()
                self._last_error = stderr
                self._log.warning("mpv exited after device selection. fallback to default: %s", stderr[:300])
                self._device_id = ""
                self._save_device_to_config()
                try:
                    self._process = subprocess.Popen(
                        self._build_command(),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        startupinfo=startupinfo,
                        creationflags=creationflags,
                    )
                except Exception as e:
                    self._last_error = str(e)
                    self._process = None
                    self._log.error("Failed to restart mpv with default device: %s", e)
                    return

        self._apply_runtime_volume_async()

    def _stop_locked(self):
        if not self._is_running_locked():
            self._process = None
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=2)
        except Exception:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
        finally:
            self._process = None

    def _restart_locked(self):
        if not self._source:
            return
        self._stop_locked()
        time.sleep(0.05)
        self._start_locked()

    def _is_running_locked(self):
        return self._process is not None and self._process.poll() is None

    def _read_process_stderr_locked(self):
        if not self._process or not self._process.stderr:
            return ""
        try:
            data = self._process.stderr.read()
            if not data:
                return ""
            return data.decode(errors="ignore")
        except Exception:
            return ""

    def _apply_runtime_volume_locked(self):
        if not HAS_PYCAW or not self._is_running_locked():
            return False
        pid = self._process.pid
        target = max(0.0, min(1.0, self._volume / 100.0))
        try:
            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                proc = session.Process
                if proc and proc.pid == pid:
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                    volume.SetMasterVolume(target, None)
                    return True
        except Exception:
            return False
        return False

    def _apply_runtime_volume_async(self):
        if not HAS_PYCAW:
            return

        def _worker():
            for _ in range(12):
                with self._lock:
                    ok = self._apply_runtime_volume_locked()
                if ok:
                    return
                time.sleep(0.1)

        threading.Thread(target=_worker, daemon=True).start()


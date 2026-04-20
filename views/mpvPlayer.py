import json
import os
import subprocess
import threading
import time
import uuid
from logging import getLogger

import constants
import globalVars
from views.audio_output_devices import getDeviceList

try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    HAS_PYCAW = True
except Exception:
    AudioUtilities = None
    ISimpleAudioVolume = None
    HAS_PYCAW = False


class MPVAudioPlayer:
    def __init__(self):
        self._log = getLogger(f"{constants.LOG_PREFIX}.MPVAudioPlayer")
        self._source = None
        self._http_headers = {}
        self._volume = 100
        self._device_id = ""
        self._start_position_sec = 0
        self._nonseekable_input = True
        self._process = None
        self._last_error = ""
        self._lock = threading.RLock()
        self._ipc_pipe_name = f"radar_mpv_{uuid.uuid4().hex}" if os.name == "nt" else ""
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
                raise ValueError("指定した再生出力は利用できません。")

            self._device_id = requested
            self._save_device_to_config()
            if self._is_running_locked():
                self._restart_locked()
            return True

    def setStartPosition(self, seconds):
        with self._lock:
            try:
                value = int(seconds)
            except Exception:
                value = 0
            self._start_position_sec = max(0, value)

    def setNonSeekableInput(self, enabled):
        with self._lock:
            self._nonseekable_input = bool(enabled)

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

    def _ipc_pipe_path(self):
        if not self._ipc_pipe_name:
            return ""
        return rf"\\.\pipe\{self._ipc_pipe_name}"

    def _build_command(self):
        lavf_opts = "reconnect=1,reconnect_streamed=1,reconnect_delay_max=2"
        if self._nonseekable_input:
            lavf_opts += ",http_seekable=0,seekable=0"
        cmd = [
            constants.MPV_PATH,
            "--no-video",
            "--force-window=no",
            "--no-terminal",
            "--keep-open=no",
            "--really-quiet",
            "--network-timeout=15",
            "--cache=yes",
            "--cache-secs=20",
            "--demuxer-readahead-secs=20",
            f"--stream-lavf-o={lavf_opts}",
            self._source,
        ]
        if self._http_headers:
            header_str = ",".join([f"{k}: {v}" for k, v in self._http_headers.items() if v])
            if header_str:
                cmd.insert(-1, f"--http-header-fields={header_str}")
        if self._ipc_pipe_name:
            cmd.insert(-1, f"--input-ipc-server={self._ipc_pipe_path()}")
        cmd.insert(-1, f"--volume={int(self._volume)}")
        if self._device_id:
            cmd.insert(-1, f"--audio-device=wasapi/{self._device_id}")
        if self._start_position_sec > 0:
            cmd.insert(-1, f"--start={self._start_position_sec}")
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
                self._last_error = stderr.strip() or "mpv exited after device selection"
                self._log.error("mpv exited after device selection: %s", self._last_error[:500])
                self._process = None
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

    def _apply_mpv_ipc_volume_locked(self):
        if not self._ipc_pipe_name or not self._is_running_locked():
            return False
        path = self._ipc_pipe_path()
        vol = max(0, min(100, int(self._volume)))
        line = json.dumps({"command": ["set_property", "volume", vol]}) + "\n"
        for _ in range(30):
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as pipe:
                    pipe.write(line)
                return True
            except OSError:
                time.sleep(0.05)
        return False

    def _apply_runtime_volume_locked(self):
        if not self._is_running_locked():
            return False
        if self._apply_mpv_ipc_volume_locked():
            return True
        if not HAS_PYCAW or ISimpleAudioVolume is None:
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
        def _worker():
            for _ in range(12):
                with self._lock:
                    ok = self._apply_runtime_volume_locked()
                if ok:
                    return
                time.sleep(0.1)

        threading.Thread(target=_worker, daemon=True).start()


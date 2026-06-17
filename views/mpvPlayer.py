import atexit
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


def _first_nonempty(environ, *keys):
    for k in keys:
        v = environ.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _norm_proxy(value):
    """手動設定の「host:port」を http:// で補完。Windows IE 複合表記は改変しない。"""
    if not value or not str(value).strip():
        return ""
    v = str(value).strip()
    if "://" in v:
        return v
    if ";" in v and "=" in v:
        return v
    return "http://" + v


def _mpv_proxy_url():
    """HTTPS ストリーム想定で mpv --http-proxy に渡す URL。"""
    val = _first_nonempty(
        os.environ, "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"
    )
    return _norm_proxy(val) if val else ""


def _spawn_proxy_env():
    """子プロセス向けにプロキシ環境変数の大小文字とスキームを揃えた env。"""
    env = os.environ.copy()
    for upper, lower in (
        ("HTTP_PROXY", "http_proxy"),
        ("HTTPS_PROXY", "https_proxy"),
    ):
        val = _first_nonempty(env, upper, lower)
        if not val:
            continue
        norm = _norm_proxy(val)
        if norm:
            env[upper] = norm
            env[lower] = norm
    no = _first_nonempty(env, "NO_PROXY", "no_proxy")
    if no:
        env["NO_PROXY"] = env["no_proxy"] = no
    return env


try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    HAS_PYCAW = True
except Exception:
    AudioUtilities = None
    ISimpleAudioVolume = None
    HAS_PYCAW = False


def shutdown_global_mpv_player():
    """RadioManager.exit を経由できない異常終了経路向けに MPV を停止する。"""
    try:
        app = getattr(globalVars, "app", None)
        if not app:
            return
        main_view = getattr(app, "hMainView", None)
        if not main_view:
            return
        radio_manager = getattr(main_view, "radio_manager", None)
        if not radio_manager:
            return
        radio_manager.exit()
    except Exception:
        pass


atexit.register(shutdown_global_mpv_player)


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

    def seekToSeconds(self, seconds):
        """mpv IPC で絶対位置シーク。成功時 True。"""
        with self._lock:
            if not self._is_running_locked():
                return False
            target = max(0, int(seconds or 0))
            # 入力中の応答性を優先し、短いリトライで複数コマンドを試す
            commands = [
                ["seek", target, "absolute"],
                ["seek", target, "absolute+exact"],
                ["set_property", "time-pos", target],
            ]
            for cmd in commands:
                if self._send_mpv_ipc_command_locked(cmd, retries=4, delay_sec=0.01):
                    return True
            return False

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
        self._log.info("MPVAudioPlayer.exit: starting shutdown")
        self.stop()
        self._log.info("MPVAudioPlayer.exit: shutdown complete")

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
        proxy_url = _mpv_proxy_url()
        if proxy_url:
            cmd.insert(-1, f"--http-proxy={proxy_url}")
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
                env=_spawn_proxy_env(),
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
            self._log.info("mpv shutdown: no running process (skip)")
            self._process = None
            return
        proc = self._process
        pid = proc.pid if proc else None
        self._log.info("mpv shutdown: starting pid=%s ipc=%s", pid, self._ipc_pipe_name or "(none)")
        used_kill = False
        try:
            proc.terminate()
            rc = proc.wait(timeout=2)
            self._log.info("mpv shutdown: exited after terminate pid=%s returncode=%s", pid, rc)
        except Exception as e_term:
            self._log.warning(
                "mpv shutdown: terminate/wait failed pid=%s: %s; trying kill",
                pid,
                e_term,
            )
            try:
                proc.kill()
                used_kill = True
                rc = proc.wait(timeout=2)
                self._log.info("mpv shutdown: exited after kill pid=%s returncode=%s", pid, rc)
            except Exception as e_kill:
                self._log.error("mpv shutdown: kill failed pid=%s: %s", pid, e_kill)
        finally:
            try:
                if proc and proc.poll() is None:
                    self._log.error(
                        "mpv still running: pid=%s kill_attempted=%s",
                        pid,
                        used_kill,
                    )
                else:
                    self._log.info("mpv shutdown: verified pid=%s poll=%s", pid, proc.poll())
            except Exception as e_poll:
                self._log.warning("mpv shutdown: exception while verifying exit pid=%s: %s", pid, e_poll)
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
        vol = max(0, min(100, int(self._volume)))
        return self._send_mpv_ipc_command_locked(["set_property", "volume", vol])

    def _send_mpv_ipc_command_locked(self, command, retries=30, delay_sec=0.05):
        if not self._ipc_pipe_name or not self._is_running_locked():
            return False
        path = self._ipc_pipe_path()
        line = json.dumps({"command": command}) + "\n"
        for _ in range(max(1, int(retries))):
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as pipe:
                    pipe.write(line)
                return True
            except OSError:
                time.sleep(max(0.0, float(delay_sec)))
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


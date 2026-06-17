#recording module
import ConfigManager
import sys
import os
import globalVars
import subprocess
import constants
import atexit
import signal
import locale
import re
import shutil
from notification_util import notify as notification_notify
from logging import getLogger
from views import token
import threading
import time
import json
import datetime
import uuid
from collections import deque
from accessible_output2.outputs.base import OutputError
from concurrent.futures import ThreadPoolExecutor
import queue
import tempfile

import simpleDialog


def _first_nonempty(environ, *keys):
    for k in keys:
        v = environ.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _norm_proxy(value):
    if not value or not str(value).strip():
        return ""
    v = str(value).strip()
    if "://" in v:
        return v
    if ";" in v and "=" in v:
        return v
    return "http://" + v


def _spawn_proxy_env():
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


logLevelSelection = {
    "50":"fatal",
    "40":"error",
    "30":"warning",
    "20":"info",
    "10":"debug",
    "0":"quiet"
}

# 定数
MAX_RETRY = 3
MAX_RECORDING_HOURS = 8
SCHEDULE_CHECK_INTERVAL = 5  # 秒
SCHEDULE_EXECUTION_WINDOW = 10  # 秒
MIN_RETRY_INTERVAL = 60  # 秒
RECORDING_END_TIME_BUFFER = 30  # 秒（ラジコの放送時刻と配信時刻のずれに対応するため、停止時刻を延長する）

# ffmpeg 起動検証: 入力オープン失敗などで即終了する場合はここで検出し、録音開始成功とみなさない
RECORDER_STARTUP_MIN_STABLE_SECONDS = 2.5  # この秒数連続でプロセスが生存し、かつ致命stderrが無いことを確認
RECORDER_STARTUP_MAX_WAIT_SECONDS = 18.0  # 遅い環境・初回バッファの最大待機

# 予約録音以外（聴き逃し・-t 指定の即時録音など）の完了履歴件数上限。「完了した録音」タブ用
MAX_MANUAL_COMPLETED_RECORDINGS = 300

# 録音ステータス定数
RECORDING_STATUS_SCHEDULED = "scheduled"  # 予約スケジュール済み
RECORDING_STATUS_RECORDING = "recording"  # 録音中
RECORDING_STATUS_COMPLETED = "completed"  # 録音が正しく完了している
RECORDING_STATUS_CANCELLED = "cancelled"  # ユーザーによってキャンセルされた
RECORDING_STATUS_FAILED = "failed"  # 予約録音がエラーによって失敗した

class RecorderError(Exception):
    """録音関連のエラー"""
    pass


class RecordingCancelledError(RecorderError):
    """ユーザー操作などによる録音中断（失敗ではない）"""
    pass


class Recorder:
    """
    レコーダー: 指定URLのストリームを指定パスに保存。エラー時はコールバックで管理者に通知。
    """
    def __init__(
        self,
        stream_url,
        output_path,
        filetype,
        on_error=None,
        logger=None,
        recording_seconds=None,
        http_headers=None,
        input_options=None,
        on_success=None,
    ):
        self.stream_url = stream_url
        self.output_path = output_path
        self.filetype = filetype
        self.on_error = on_error
        self.on_success = on_success
        self.logger = logger or getLogger("recorder")
        self.process = None
        self.recording = False
        self._stop_event = threading.Event()
        self.last_ffmpeg_cmd = ""
        self._stderr_lines = deque(maxlen=120)
        self._stderr_lock = threading.Lock()
        self._stderr_thread = None
        self.recording_seconds = recording_seconds
        self.http_headers = dict(http_headers or {})
        self.input_options = list(input_options or [])
        self._completion_cancelled = False

    def _wait_stderr_drain(self, timeout=2.0):
        """stderr 読み取りスレッドが追いつくまで待つ（プロセス終了直後の取りこぼし防止）"""
        t = getattr(self, "_stderr_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def _read_process_stderr_chunk(self, proc, max_bytes=32768):
        """終了後などに stderr から最大 max_bytes バイト読む（起動失敗メッセージ用）"""
        try:
            if proc and proc.stderr:
                raw = proc.stderr.read(max_bytes)
                return raw.decode(errors="replace").strip()
        except Exception as e:
            self.logger.debug(f"Could not read ffmpeg stderr: {e}")
        return ""

    def _dispose_failed_startup_process(self):
        """起動失敗時に子プロセスとパイプを後片付けする"""
        proc = self.process
        if proc is None:
            self.recording = False
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        except Exception as e:
            self.logger.warning(f"dispose_failed_startup_process: {e}")
        finally:
            self._wait_stderr_drain(timeout=1.5)
            for attr in ("stdin", "stdout", "stderr"):
                pipe = getattr(proc, attr, None)
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            self.process = None
            self.recording = False

    def _format_startup_failure_message(self, return_code, stderr_text):
        """ユーザー向け・ログ向けの起動失敗メッセージ"""
        st = stderr_text or ""
        hint = ""
        if "-138" in st or "Error number -138" in st:
            hint = (
                "\nネットワーク接続に失敗しました（タイムアウト等）。"
                "インターネット接続・VPN・ファイアウォール・サーバー混雑を確認してください。"
            )
        elif "Connection refused" in st:
            hint = "\n接続が拒否されました。"
        elif "403" in st or "Forbidden" in st:
            hint = "\nアクセスが拒否されている可能性があります（認証・地域制限など）。"
        detail = st[:2000] if st else "(標準エラー出力なし)"
        if return_code is not None:
            head = f"録音を開始できませんでした（ffmpeg が異常終了しました）。終了コード: {return_code}"
        else:
            head = "録音を開始できませんでした（ffmpeg が入力ストリームを開けませんでした）。"
        return f"{head}{hint}\n\n{detail}"

    def _fatal_ffmpeg_stderr_excerpt(self):
        """ffmpeg が致命エラーを stderr に出している場合、その内容を返す（なければ None）。"""
        markers = (
            "[tcp @",
            "error opening input file",
            "error opening input files",
            "error opening input:",
            "unable to open",
            "connection timed out",
            "connection refused",
            "connection failed",
            "connection to tcp://",
            "failed:",
            "error number",
            "server returned 403",
            "server returned 404",
            "http error",
            "invalid argument",
            "invalid data found when processing input",
            "no such file or directory",
        )
        with self._stderr_lock:
            text = "\n".join(self._stderr_lines)
        if not text.strip():
            return None
        blob = text.lower()
        for m in markers:
            if m in blob:
                return text.strip()[-4000:]
        return None

    def _abort_startup_for_fatal_stderr(self):
        """検証中に致命stderrを検出したときの後処理と例外送出"""
        excerpt = self._fatal_ffmpeg_stderr_excerpt()
        if excerpt is None:
            return
        msg = self._format_startup_failure_message(None, excerpt)
        self.logger.error(f"FFmpeg fatal stderr during startup verification: {msg}")
        self._dispose_failed_startup_process()
        raise RecorderError(msg)

    def _verify_process_stable_startup(self):
        """
        ffmpeg が TCP / 入力オープン失敗ですぐ終了するケースを検出する。
        プロセスが RECORDER_STARTUP_MIN_STABLE_SECONDS 連続で生存していることを確認してから戻る。
        """
        proc = self.process
        if proc is None:
            raise RecorderError("録音プロセスが開始されていません。")

        deadline = time.monotonic() + RECORDER_STARTUP_MAX_WAIT_SECONDS
        stable_deadline = None

        while time.monotonic() < deadline:
            self._abort_startup_for_fatal_stderr()

            code = proc.poll()
            if code is not None:
                self._wait_stderr_drain(timeout=2.5)
                stderr_text = self._get_recent_stderr().strip()
                if not stderr_text:
                    stderr_text = self._read_process_stderr_chunk(proc)
                if not stderr_text.strip():
                    stderr_text = (
                        "ffmpeg が異常終了しましたが、標準エラー出力を取得できませんでした。"
                        "（TCP / HLS の接続に失敗した可能性があります。）"
                    )
                msg = self._format_startup_failure_message(code, stderr_text)
                self.logger.error(f"FFmpeg startup failure: {msg}")
                self._dispose_failed_startup_process()
                raise RecorderError(msg)

            # 生存中
            now = time.monotonic()
            if stable_deadline is None:
                stable_deadline = now + RECORDER_STARTUP_MIN_STABLE_SECONDS
            if now >= stable_deadline:
                self._abort_startup_for_fatal_stderr()
                self.logger.info(
                    f"FFmpeg startup verified stable for {RECORDER_STARTUP_MIN_STABLE_SECONDS}s "
                    f"(pid={proc.pid})"
                )
                return

            time.sleep(0.1)

        # 最大待機まで終了しなかった（まだ動いている）→ 致命stderrが無ければ成功とみなす
        self._abort_startup_for_fatal_stderr()
        code = proc.poll()
        if code is not None:
            self._wait_stderr_drain(timeout=2.5)
            stderr_text = self._get_recent_stderr().strip()
            if not stderr_text:
                stderr_text = self._read_process_stderr_chunk(proc)
            if not stderr_text.strip():
                stderr_text = (
                    "ffmpeg が異常終了しましたが、標準エラー出力を取得できませんでした。"
                    "（TCP / HLS の接続に失敗した可能性があります。）"
                )
            msg = self._format_startup_failure_message(code, stderr_text)
            self.logger.error(f"FFmpeg startup failure at deadline: {msg}")
            self._dispose_failed_startup_process()
            raise RecorderError(msg)

        self.logger.info(f"FFmpeg startup verified still running after max wait (pid={proc.pid})")

    def start(self):
        """録音を開始"""
        try:
            self.logger.info(f"Start recording: {self.stream_url} -> {self.output_path}.{self.filetype}")
            ffmpeg_path = self._get_ffmpeg_path()

            # 出力先ディレクトリを保証
            self._ensure_output_directory()

            cmd = self._build_ffmpeg_command(ffmpeg_path)
            self.last_ffmpeg_cmd = " ".join(cmd)
            self.logger.debug(f"FFmpeg command: {' '.join(cmd)}")
            # Windows環境でffmpegプロンプトを非表示にする
            startupinfo = None
            if os.name == 'nt':  # Windows
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                env=_spawn_proxy_env(),
            )
            self.logger.info(f"FFmpeg process spawned PID: {self.process.pid}")

            # stderr を検証より先に読み始める（パイプ詰まりによる偽生存を防ぎ、致命ログを検出する）
            self._stderr_thread = threading.Thread(target=self._consume_stderr, daemon=True)
            self._stderr_thread.start()
            time.sleep(0.05)

            self._verify_process_stable_startup()

            self._abort_startup_for_fatal_stderr()

            self.recording = True
            threading.Thread(target=self._monitor, daemon=True).start()
        except RecorderError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to start recording: {e}")
            self._dispose_failed_startup_process()
            self._notify_error(e)
            raise


    def stop(self):
        """録音を安全に停止"""
        self.logger.info(f"Stop requested for: {self.output_path}.{self.filetype}")
        self._stop_event.set()
        
        if self.process and self.process.poll() is None:
            try:
                # ffmpegへ明示的に終了シグナルを送る
                if self.process.stdin:
                    try:
                        self.process.stdin.write(b"q\n")
                        self.process.stdin.flush()
                    except Exception:
                        # stdin送信に失敗した場合は terminate にフォールバック
                        pass
                    finally:
                        try:
                            self.process.stdin.close()
                        except Exception:
                            pass
                
                # 終了を待つ
                try:
                    self.process.wait(timeout=5)
                    self.logger.info(f"Process terminated gracefully: {self.output_path}.{self.filetype}")
                except subprocess.TimeoutExpired:
                    self.logger.warning("Graceful stop timed out, trying terminate")
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=3)
                        self.logger.info(f"Process terminated after terminate(): {self.output_path}.{self.filetype}")
                    except subprocess.TimeoutExpired:
                        self.logger.warning("Terminate failed, killing process")
                        self.process.kill()
                        self.process.wait(timeout=2)
                        self.logger.info(f"Process killed forcefully: {self.output_path}.{self.filetype}")
                    except Exception as e:
                        self.logger.error(f"Kill failed: {e}")
                        
            except Exception as e:
                self.logger.error(f"Error during process termination: {e}")
        
        # 状態をリセット
        self.recording = False
        self.process = None
        self.logger.info(f"Recording stopped and instance destroyed: {self.output_path}.{self.filetype}")

    def _monitor(self):
        """録音プロセスの監視"""
        try:
            self.logger.info(f"Starting process monitoring for: {self.output_path}.{self.filetype}")
            proc = self.process
            if not proc:
                raise RecorderError("Recording process is not initialized.")
            while not self._stop_event.is_set():
                if proc.poll() is not None:
                    # プロセスが終了
                    if self._stop_event.is_set():
                        # 正常終了（stop()が呼ばれた場合）
                        self.logger.info(f"Recording process stopped normally: {self.output_path}.{self.filetype}")
                        break
                    if proc.returncode == 0 and self.recording_seconds:
                        # -t で予定終了した場合
                        self.logger.info(f"Recording process finished by duration limit: {self.output_path}.{self.filetype}")
                        if self.on_success:
                            try:
                                self.on_success(self)
                            except Exception as cb_e:
                                self.logger.error(f"on_success callback failed: {cb_e}")
                        break
                    else:
                        # 異常終了
                        return_code = proc.returncode
                        stderr = self._get_recent_stderr()
                        if not stderr.strip():
                            stderr = (
                                f"(empty stderr) returncode={return_code}, "
                                f"cmd={self.last_ffmpeg_cmd}"
                            )
                        self.logger.error(
                            f"Recording process exited unexpectedly: {self.output_path}.{self.filetype}, "
                            f"returncode={return_code}, stderr: {stderr}"
                        )
                        raise RecorderError(f"Recording process exited unexpectedly: {stderr}")
                time.sleep(1)
        except Exception as e:
            self.logger.error(f"Monitor error: {e}")
            self._notify_error(e)
        finally:
            self.recording = False
            self.logger.info(f"Process monitoring ended for: {self.output_path}.{self.filetype}")

    def _build_ffmpeg_command(self, ffmpeg_path):
        """録音用ffmpegコマンドを構築"""
        quality_settings = self._get_quality_settings()
        output_file = f"{self.output_path}.{self.filetype}"
        header_string = ""
        if self.http_headers:
            header_lines = [f"{k}: {v}" for k, v in self.http_headers.items() if v]
            if header_lines:
                header_string = "\r\n".join(header_lines) + "\r\n"
        return [
            ffmpeg_path,
            "-hide_banner",
            "-nostats",
            "-y",
            "-loglevel", "error",
            "-fflags", "+discardcorrupt",
        ] + (
            ["-headers", header_string] if header_string else []
        ) + self.input_options + [
            "-i", self.stream_url,
        ] + quality_settings + (
            ["-t", str(int(self.recording_seconds))] if self.recording_seconds else []
        ) + [
            "-vn",
            output_file
        ]

    def _ensure_output_directory(self):
        """出力ディレクトリが存在しない場合は作成"""
        output_dir = os.path.dirname(self.output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

    def _consume_stderr(self):
        """ffmpeg の stderr を読む（readline 依存をやめ、\\r のみの行やバッファ分割にも対応）"""
        proc = self.process
        if not proc or not proc.stderr:
            return
        buf = b""
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]+", buf)
                buf = parts[-1]
                for part in parts[:-1]:
                    text = part.decode(errors="replace").strip()
                    if text:
                        with self._stderr_lock:
                            self._stderr_lines.append(text)
                # 改行が長く来ないログでも検出できるよう、バッファが膨らんだら強制フラッシュ
                if len(buf) > 16384:
                    spill = buf[:-4096]
                    buf = buf[-4096:]
                    text = spill.decode(errors="replace").strip()
                    if text:
                        with self._stderr_lock:
                            self._stderr_lines.append(text)
            if buf.strip():
                text = buf.decode(errors="replace").strip()
                if text:
                    with self._stderr_lock:
                        self._stderr_lines.append(text)
        except Exception as e:
            self.logger.debug(f"Failed to consume ffmpeg stderr: {e}")

    def _get_recent_stderr(self):
        """保持しているstderrの末尾を文字列化"""
        with self._stderr_lock:
            if not self._stderr_lines:
                return ""
            return "\n".join(self._stderr_lines)

    def _notify_error(self, error):
        """エラーを管理者に通知"""
        if self.on_error:
            self.on_error(self, error)

    def _get_ffmpeg_path(self):
        """利用可能なffmpegのパスを取得（起動確認付き）"""
        candidates = []

        # 1) 設定済みパス
        if constants.FFMPEG_PATH:
            candidates.append(constants.FFMPEG_PATH)

        # 2) ワークスペース直下のffmpeg.exe
        candidates.append(os.path.abspath("ffmpeg.exe"))

        # 3) PATH上のffmpeg
        path_ffmpeg = shutil.which("ffmpeg")
        if path_ffmpeg:
            candidates.append(path_ffmpeg)

        tried = []
        failed_reasons = []
        for candidate in candidates:
            if not candidate:
                continue
            normalized = os.path.abspath(candidate)
            if normalized in tried:
                continue
            tried.append(normalized)

            if not os.path.exists(normalized):
                continue

            ok, reason = self._validate_ffmpeg_binary(normalized)
            if ok:
                if normalized != os.path.abspath(constants.FFMPEG_PATH):
                    self.logger.warning(f"Using fallback ffmpeg binary: {normalized}")
                return normalized
            self.logger.warning(f"ffmpeg validation failed: {normalized} ({reason})")
            failed_reasons.append(f"{normalized}: {reason}")

        detail = "; ".join(failed_reasons[:3])
        raise RecorderError(
            "利用可能なffmpegが見つからないか、ffmpeg起動に必要なDLLが不足しています。"
            " ffmpegの再配置またはPATH上のffmpegを確認してください。"
            + (f" 詳細: {detail}" if detail else "")
        )

    def _validate_ffmpeg_binary(self, ffmpeg_path):
        """ffmpegが実際に起動できるか検証"""
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        try:
            proc = subprocess.run(
                [ffmpeg_path, "-version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                timeout=5,
                check=False
            )
        except Exception as e:
            return False, f"exception={e}"

        if proc.returncode == 0:
            return True, ""

        stderr = (proc.stderr or b"").decode(errors="ignore").strip()
        code = proc.returncode
        # WindowsのSTATUS_DLL_NOT_FOUND: -1073741515 (0xC0000135)
        if code in (-1073741515, 3221225781):
            return False, "STATUS_DLL_NOT_FOUND (0xC0000135)"
        if not stderr:
            return False, f"returncode={code}"
        return False, f"returncode={code}, stderr={stderr[:200]}"

    def _get_quality_settings(self):
        """ファイルタイプに応じた音質設定を取得"""
        if self.filetype == "wav":
            # WAV: 最高音質、48kHz、24bit、ステレオ
            return [
                "-acodec", "pcm_s24le",
                "-ar", "48000",
                "-ac", "2"
            ]
        elif self.filetype == "mp3":
            # MP3: 高品質、192kbps、44.1kHz、ステレオ
            return [
                "-acodec", "libmp3lame",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2"
            ]
        elif self.filetype == "m4a":
            # M4A: ラジコAACを再エンコードせずに保存
            return [
                "-acodec", "copy",
                "-bsf:a", "aac_adtstoasc"
            ]
        else:
            # デフォルト設定（コピー）
            return [
                "-acodec", "copy"
            ]

    def is_recording(self):
        """録音中かどうかを返す"""
        return self.recording


def _ffmpeg_header_option(headers):
    if not headers:
        return []
    lines = [f"{k}: {v}" for k, v in dict(headers).items() if v]
    if not lines:
        return []
    return ["-headers", "\r\n".join(lines) + "\r\n"]


def _ffmpeg_output_quality_args(filetype):
    if filetype == "wav":
        return ["-acodec", "pcm_s24le", "-ar", "48000", "-ac", "2"]
    if filetype == "mp3":
        return ["-acodec", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
    if filetype == "m4a":
        return ["-acodec", "copy", "-bsf:a", "aac_adtstoasc"]
    return ["-acodec", "copy"]


class TimefreeChunkedRecordingHandle:
    """
    聴き逃し（playlist_create_url）の l<=300 秒制限に対応する分割取得 + concat。
    中間は常に AAC コピー（m4a）で結合し、最後にユーザー指定形式へ変換する。
    """

    def __init__(self, manager, segments, output_path, filetype, input_options, logger, info):
        self.manager = manager
        self.segments = segments
        self.output_path = output_path
        self.filetype = (filetype or "mp3").lower().lstrip(".")
        self.input_options = list(input_options or [])
        self.logger = logger or getLogger("recorder")
        self.info = info
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._proc_lock = threading.Lock()
        self._done = False
        self._completion_cancelled = False

    def is_recording(self):
        if self._done:
            return False
        t = self._thread
        return bool(t and t.is_alive() and not self._stop.is_set())

    def start(self):
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._proc_lock:
            p = self._proc
        if p and p.poll() is None:
            try:
                if p.stdin:
                    try:
                        p.stdin.write(b"q\n")
                        p.stdin.flush()
                    except Exception:
                        pass
                    finally:
                        try:
                            p.stdin.close()
                        except Exception:
                            pass
                p.wait(timeout=4)
            except Exception:
                try:
                    p.terminate()
                    p.wait(timeout=3)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        if self._thread:
            self._thread.join(timeout=0.2)

    def _popen_ffmpeg(self, cmd):
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=_spawn_proxy_env(),
        )

    def _run_ffmpeg(self, cmd):
        proc = self._popen_ffmpeg(cmd)
        with self._proc_lock:
            self._proc = proc
        try:
            _, err_b = proc.communicate()
            err = (err_b or b"").decode(errors="replace")
            if proc.returncode != 0:
                if self._stop.is_set():
                    raise RecordingCancelledError("聴き逃し録音が中断されました。")
                raise RecorderError(
                    f"ffmpeg failed (exit {proc.returncode}): {(err or '').strip()[-2000:]}"
                )
        finally:
            with self._proc_lock:
                self._proc = None

    def _run(self):
        tmp = None
        try:
            tmp = tempfile.mkdtemp(prefix="radar_tf_")
            probe = Recorder("", "", "m4a", logger=self.logger)
            ffmpeg_path = probe._get_ffmpeg_path()
            chunk_paths = []
            for i, (url, hdr, sec) in enumerate(self.segments):
                if self._stop.is_set():
                    raise RecordingCancelledError("聴き逃し録音が中断されました。")
                part = os.path.join(tmp, f"part{i:04d}.m4a")
                cmd = [
                    ffmpeg_path,
                    "-hide_banner",
                    "-nostats",
                    "-y",
                    "-loglevel",
                    "error",
                    "-fflags",
                    "+discardcorrupt",
                ]
                cmd.extend(_ffmpeg_header_option(hdr))
                cmd.extend(self.input_options)
                cmd.extend([
                    "-i",
                    url,
                    "-t",
                    str(int(sec)),
                    "-vn",
                    "-acodec",
                    "copy",
                    "-bsf:a",
                    "aac_adtstoasc",
                    part,
                ])
                self.logger.info("timefree chunk %s/%s (%ss)", i + 1, len(self.segments), sec)
                self._run_ffmpeg(cmd)
                chunk_paths.append(part)
            if self._stop.is_set():
                raise RecordingCancelledError("聴き逃し録音が中断されました。")
            list_path = os.path.join(tmp, "concat.txt")
            with open(list_path, "w", encoding="utf-8") as f:
                for pth in chunk_paths:
                    ap = os.path.abspath(pth).replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{ap}'\n")
            merged = os.path.join(tmp, "merged.m4a")
            concat_cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-nostats",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c",
                "copy",
                merged,
            ]
            self._run_ffmpeg(concat_cmd)
            final = f"{self.output_path}.{self.filetype}"
            if self.filetype == "m4a":
                if os.path.exists(final):
                    os.remove(final)
                shutil.move(merged, final)
            else:
                transcode_cmd = [
                    ffmpeg_path,
                    "-hide_banner",
                    "-nostats",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    merged,
                ]
                transcode_cmd.extend(_ffmpeg_output_quality_args(self.filetype))
                transcode_cmd.append(final)
                self._run_ffmpeg(transcode_cmd)
            self.logger.info("Timefree chunked recording completed: %s", final)
            self.manager.append_manual_completed_recording(
                self.info,
                self.output_path,
                self.filetype,
                getattr(self, "_started_at", time.time()),
                time.time(),
            )
        except RecordingCancelledError as e:
            self.logger.info("Timefree chunked recording cancelled: %s", e)
        except Exception as e:
            self.logger.error("Timefree chunked recording failed: %s", e)
            try:
                notification_notify(
                    title="録音失敗",
                    message=f"{self.info} の聴き逃し録音に失敗しました。",
                    app_name="rpb",
                    timeout=10,
                )
            except Exception:
                pass
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            with self.manager.lock:
                self.manager.recorders = [
                    r for r in self.manager.recorders if r["recorder"] is not self
                ]
            self._done = True


def normalize_program_title_for_dedup(title):
    """番組名の表記ゆれを吸収して重複録音判定に使う（UI側の禁止文字処理と揃える）。"""
    if title is None:
        return ""
    t = re.sub(r'[<>:"/\\|?*]', "_", str(title)).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def allocate_unique_output_base(output_path_without_ext, filetype, logger=None):
    """
    指定拡張子のファイルが既に存在する場合は _1, _2 … を付けて衝突しないベースパスを返す。
    （別プロセスが同名ファイルを掴んでいる場合や、秒単位タイムスタンプの重複を回避）
    """
    if not output_path_without_ext or not str(output_path_without_ext).strip():
        raise ValueError("allocate_unique_output_base: empty output path")
    output_path_without_ext = os.path.normpath(output_path_without_ext)
    ext = filetype.lower().lstrip(".")
    candidate = output_path_without_ext
    n = 1
    while os.path.exists(f"{candidate}.{ext}"):
        candidate = f"{output_path_without_ext}_{n}"
        n += 1
        if n > 10000:
            raise RuntimeError("Could not allocate unique recording output path")
    if candidate != output_path_without_ext and logger:
        logger.info(
            "Recording output path adjusted to avoid existing file: "
            f"{output_path_without_ext}.{ext} -> {candidate}.{ext}"
        )
    return candidate


class RecorderManager:
    """
    レコーダー管理者: レコーダーの起動・監督・エラー処理・安全な停止・状態取得
    """
    def __init__(self, logger=None):
        self.logger = logger or getLogger("recorder_manager")
        self.recorders = []  # [{recorder, info, retry_count, end_time}]
        self.lock = threading.Lock()
        self.max_hours = MAX_RECORDING_HOURS
        self.last_start_error = ""
        self.manual_completed_recordings = []

    def append_manual_completed_recording(self, info, output_path, filetype, start_ts, end_ts=None):
        """予約録音以外で正常完了した録音を「完了した録音」用に記録する。"""
        entry = {
            "info": info or "",
            "output_path": output_path,
            "filetype": (filetype or "mp3").lower().lstrip("."),
            "start_time": float(start_ts),
            "end_time": float(end_ts if end_ts is not None else time.time()),
        }
        with self.lock:
            self.manual_completed_recordings.insert(0, entry)
            if len(self.manual_completed_recordings) > MAX_MANUAL_COMPLETED_RECORDINGS:
                self.manual_completed_recordings = self.manual_completed_recordings[:MAX_MANUAL_COMPLETED_RECORDINGS]

    def get_manual_completed_recordings(self):
        with self.lock:
            return [dict(x) for x in self.manual_completed_recordings]

    def _recorder_matches_station(self, rec_entry, station_id):
        """放送局が一致するか。エントリに station_id があれば ID で比較し、なければ info への包含でフォールバック。"""
        if station_id is None:
            return False
        entry_sid = rec_entry.get("station_id")
        if entry_sid is not None and entry_sid != "":
            return str(entry_sid) == str(station_id)
        info = rec_entry.get("info") or ""
        return station_id in info

    def start_recording(self, stream_url, output_path, info, end_time, filetype="mp3", on_complete=None, station_id=None, program_title=None, schedule_id=None, recording_seconds=None, http_headers=None, input_options=None):
        """録音を開始"""
        try:
            self.last_start_error = ""
            output_path = allocate_unique_output_base(output_path, filetype, self.logger)

            def on_error(rec, error):
                self._handle_error(rec, error, info, stream_url, output_path, end_time, filetype, recording_seconds, http_headers, input_options)

            start_ts = time.time()

            def on_success(rec):
                self.append_manual_completed_recording(
                    info,
                    rec.output_path,
                    rec.filetype,
                    start_ts,
                    time.time(),
                )

            recorder = Recorder(
                stream_url,
                output_path,
                filetype,
                on_error=on_error,
                logger=self.logger,
                recording_seconds=recording_seconds,
                http_headers=http_headers,
                input_options=input_options,
                on_success=on_success,
            )
            recorder.start()

            with self.lock:
                self.recorders.append({
                    "recorder": recorder,
                    "info": info,
                    "retry_count": 0,
                    "end_time": end_time,
                    "on_complete": on_complete,
                    "station_id": station_id,
                    "program_title": program_title,
                    "schedule_id": schedule_id,
                    "start_time": time.time(),
                    "recording_seconds": recording_seconds,
                    "http_headers": dict(http_headers or {}),
                    "input_options": list(input_options or [])
                })
            # 終了タイマー
            threading.Thread(target=self._schedule_stop, args=(recorder, end_time, on_complete), daemon=True).start()
            self.logger.info(f"Recorder started: {info}")
            return recorder
        except Exception as e:
            self.last_start_error = str(e)
            self.logger.error(f"Failed to start recording: {e}")
            return None

    def start_timefree_recording_segments(
        self,
        segments,
        output_path,
        info,
        end_time,
        filetype="mp3",
        station_id=None,
        program_title=None,
        input_options=None,
    ):
        """聴き逃し録音: 単一セグメントは通常 Recorder、複数は 300 秒単位の分割 + concat。"""
        if not segments:
            self.last_start_error = "聴き逃し録音のセグメントがありません。"
            return None
        if len(segments) == 1:
            url, hdr, sec = segments[0]
            return self.start_recording(
                url,
                output_path,
                info,
                end_time,
                filetype,
                station_id=station_id,
                program_title=program_title,
                recording_seconds=sec,
                http_headers=hdr,
                input_options=input_options,
            )
        try:
            self.last_start_error = ""
            output_path = allocate_unique_output_base(output_path, filetype, self.logger)
            handle = TimefreeChunkedRecordingHandle(
                self,
                segments,
                output_path,
                filetype,
                input_options,
                self.logger,
                info,
            )
            handle.start()
            with self.lock:
                self.recorders.append({
                    "recorder": handle,
                    "info": info,
                    "retry_count": 0,
                    "end_time": end_time,
                    "on_complete": None,
                    "station_id": station_id,
                    "program_title": program_title,
                    "start_time": time.time(),
                    "recording_seconds": sum(s[2] for s in segments),
                    "http_headers": {},
                    "input_options": list(input_options or []),
                })
            threading.Thread(
                target=self._schedule_stop,
                args=(handle, end_time, None),
                daemon=True,
            ).start()
            self.logger.info(f"Timefree chunked recorder started: {info} ({len(segments)} segments)")
            return handle
        except Exception as e:
            self.last_start_error = str(e)
            self.logger.error(f"Failed to start timefree chunked recording: {e}")
            return None

    def get_last_start_error(self):
        """最後の録音開始エラーを取得"""
        return self.last_start_error

    def _schedule_stop(self, recorder, end_time, on_complete=None):
        """指定時刻に録音を停止"""
        now = time.time()
        wait = max(0, end_time - now)
        max_wait = self.max_hours * 3600
        wait = min(wait, max_wait)
        self.logger.debug(f"Recorder will stop in {wait} seconds.")
        time.sleep(wait)
        if getattr(recorder, "_completion_cancelled", False):
            self.logger.info(
                f"Skipping scheduled stop/completion (manual stop): {getattr(recorder, 'output_path', recorder)}"
            )
            return
        recorder.stop()
        self.logger.info(f"Recorder stopped by schedule: {recorder.output_path}")
        
        # レコーダーインスタンスをリストから削除
        with self.lock:
            self.recorders = [r for r in self.recorders if r["recorder"] != recorder]
            self.logger.info(f"Recorder instance removed from manager: {recorder.output_path}")
        
        # 録音完了コールバックを呼び出し
        if on_complete:
            try:
                on_complete(recorder)
                self.logger.info(f"Recording completion callback executed successfully: {recorder.output_path}")
            except Exception as e:
                self.logger.error(f"Error in recording completion callback: {e}")

    def _handle_error(self, recorder, error, info, stream_url, output_path, end_time, filetype, recording_seconds=None, http_headers=None, input_options=None):
        """エラー処理とリトライ"""
        should_retry = False
        retry_path = output_path
        on_complete = None
        retry_station_id = None
        retry_program_title = None
        with self.lock:
            rec_entry = next((r for r in self.recorders if r["recorder"] == recorder), None)
            if not rec_entry:
                self.logger.error("Error from unknown recorder.")
                return
            rec_entry["retry_count"] += 1
            retry = rec_entry["retry_count"]
            self.logger.warning(f"Recorder error (attempt {retry}): {error}")
            recorder.stop()

            # ファイル名変更してリトライ
            if os.path.exists(f"{output_path}.{filetype}"):
                new_path = f"{output_path}_retry{retry}"
            else:
                new_path = output_path

            # ffmpeg実行環境の不備はリトライしても復旧しないため即失敗にする
            error_text = str(error)
            non_retryable = (
                "STATUS_DLL_NOT_FOUND" in error_text or
                "ffmpeg validation failed" in error_text or
                "利用可能なffmpegが見つからない" in error_text
            )

            if retry < MAX_RETRY and not non_retryable:
                self.logger.info(f"Retrying recording: {new_path}")
                # 元のコールバックを保持してリトライ
                on_complete = rec_entry.get("on_complete")
                should_retry = True
                retry_path = new_path
                retry_station_id = rec_entry.get("station_id")
                retry_program_title = rec_entry.get("program_title")
                retry_schedule_id = rec_entry.get("schedule_id")
            else:
                self.logger.error(f"Recording failed after {MAX_RETRY} attempts: {info}")
                try:
                    notification_notify(title="録音失敗", message=f"{info} の録音に失敗しました。", app_name="rpb", timeout=10)
                    self.logger.info(f"Recording failure notification sent successfully after max retries: {info}")
                except Exception as e:
                    self.logger.error(f"Failed to send recording failure notification after max retries: {e}")

            # 失敗したエントリは必ず取り除く（ゾンビ状態で局が占有されたままになるのを防ぐ）
            self.recorders = [r for r in self.recorders if r["recorder"] != recorder]

        # start_recording は lock の外で実行（デッドロック防止）
        if should_retry:
            self.start_recording(
                stream_url,
                retry_path,
                info,
                end_time,
                filetype,
                on_complete,
                station_id=retry_station_id,
                program_title=retry_program_title,
                schedule_id=retry_schedule_id,
                recording_seconds=recording_seconds,
                http_headers=http_headers,
                input_options=input_options,
            )

    def _is_recorder_active(self, recorder):
        """録音中判定を安全に実行"""
        try:
            if isinstance(recorder, Recorder):
                return bool(recorder.recording)
            if hasattr(recorder, "is_recording"):
                return bool(recorder.is_recording())
        except RecursionError:
            self.logger.error("RecursionError detected while checking recorder state.")
        except Exception as e:
            self.logger.error(f"Failed to check recorder state: {e}")
        return False

    def _mark_manual_stop(self, rec_entry):
        """ユーザー操作による停止: 終了予定時刻の完了コールバック・通知を抑止する。"""
        recorder = rec_entry.get("recorder")
        if recorder is not None:
            recorder._completion_cancelled = True
        if rec_entry.get("on_complete"):
            schedule_manager.mark_recording_interrupted(rec_entry)

    def _stop_recorder_entry_thread(self, rec_entry):
        """manager.lock を握らずに recorder.stop() する（デッドロック・UI フリーズ防止）。"""
        info = rec_entry.get("info", "")
        try:
            rec_entry["recorder"].stop()
            self.logger.info(f"Recorder stopped successfully: {info}")
        except Exception as e:
            self.logger.error(f"Error stopping recorder {info}: {e}")

    def _pop_and_stop_entries(self, entries):
        """手動停止マーク後に非同期で stop する。"""
        for rec_entry in entries:
            self._mark_manual_stop(rec_entry)
            threading.Thread(
                target=self._stop_recorder_entry_thread,
                args=(rec_entry,),
                daemon=True,
            ).start()

    def stop_all(self, wait=False):
        """全ての録音を停止。wait=True は終了時など停止完了を待つ場合に限る。"""
        with self.lock:
            active_count = len(self.recorders)
            self.logger.info(f"Stopping all {active_count} active recorders")
            entries = list(self.recorders)
            self.recorders.clear()
        for rec_entry in entries:
            self._mark_manual_stop(rec_entry)
        threads = []
        for rec_entry in entries:
            t = threading.Thread(
                target=self._stop_recorder_entry_thread,
                args=(rec_entry,),
                daemon=True,
            )
            t.start()
            threads.append(t)
        if wait:
            for thread in threads:
                thread.join(timeout=30)
            self.logger.info(f"All {active_count} recorders stop threads joined.")
        else:
            self.logger.info(f"Dispatched stop for {active_count} recorders (no wait).")

    def stop_recorder(self, recorder):
        """指定された録音を一覧から外し、別スレッドで stop を実行する。"""
        removed = None
        with self.lock:
            for i, rec_entry in enumerate(self.recorders):
                if rec_entry["recorder"] == recorder:
                    removed = self.recorders.pop(i)
                    self.logger.info(f"Recorder removed from list, stopping async: {removed['info']}")
                    break
        if removed:
            self._mark_manual_stop(removed)
            threading.Thread(
                target=self._stop_recorder_entry_thread,
                args=(removed,),
                daemon=True,
            ).start()

    def get_active_recorders(self):
        """アクティブな録音の一覧を取得"""
        with self.lock:
            return [(r["recorder"], r["info"]) for r in self.recorders if self._is_recorder_active(r["recorder"])]

    def get_station_recorders(self, station_id):
        """指定された放送局の録音一覧を取得"""
        with self.lock:
            return [r for r in self.recorders if self._recorder_matches_station(r, station_id)]

    def is_station_recording(self, station_id):
        """指定された放送局に録音エントリがあるか（起動直後など recording フラグが未更新でも検出する）"""
        with self.lock:
            return any(self._recorder_matches_station(r, station_id) for r in self.recorders)

    def stop_station_recording(self, station_id):
        """指定された放送局の録音を停止"""
        to_stop = []
        with self.lock:
            for i in range(len(self.recorders) - 1, -1, -1):
                rec_entry = self.recorders[i]
                if not self._recorder_matches_station(rec_entry, station_id):
                    continue
                to_stop.append(self.recorders.pop(i))
        for rec_entry in to_stop:
            self.logger.info(f"Stopping recording for station {station_id}: {rec_entry['info']}")
        self._pop_and_stop_entries(to_stop)
        return len(to_stop)

    def stop_recording_for_program(self, station_id, program_title):
        """放送局IDと番組タイトルが一致する録音を停止（予約録音の取り消しなどに使用）"""
        if station_id is None or program_title is None:
            return 0
        want = normalize_program_title_for_dedup(program_title)
        to_stop = []
        with self.lock:
            for i in range(len(self.recorders) - 1, -1, -1):
                rec_entry = self.recorders[i]
                if not self._recorder_matches_station(rec_entry, station_id):
                    continue
                if not self._is_recorder_active(rec_entry["recorder"]):
                    continue
                rt = rec_entry.get("program_title")
                if rt is not None and rt != "":
                    if normalize_program_title_for_dedup(rt) != want:
                        continue
                else:
                    info_n = normalize_program_title_for_dedup(rec_entry.get("info") or "")
                    if not want or want not in info_n:
                        continue
                to_stop.append(self.recorders.pop(i))
                self.logger.info(f"Recorder queued for async stop (scheduled program): {rec_entry['info']}")
        self._pop_and_stop_entries(to_stop)
        return len(to_stop)

    def is_duplicate_recording(self, station_id, program_title):
        """同じ放送局・同じ番組の重複録音をチェック"""
        want = normalize_program_title_for_dedup(program_title)
        with self.lock:
            for rec_entry in self.recorders:
                if not self._is_recorder_active(rec_entry["recorder"]):
                    continue
                if not self._recorder_matches_station(rec_entry, station_id):
                    continue
                rt = rec_entry.get("program_title")
                if rt is not None and rt != "":
                    if normalize_program_title_for_dedup(rt) == want:
                        return True
                else:
                    info_n = normalize_program_title_for_dedup(rec_entry.get("info") or "")
                    if want and want in info_n:
                        return True
            return False

    def get_recording_info(self, station_id, program_title):
        """指定された放送局・番組の録音情報を取得"""
        want = normalize_program_title_for_dedup(program_title)
        with self.lock:
            for rec_entry in self.recorders:
                if not self._is_recorder_active(rec_entry["recorder"]):
                    continue
                if not self._recorder_matches_station(rec_entry, station_id):
                    continue
                rt = rec_entry.get("program_title")
                if rt is not None and rt != "":
                    if normalize_program_title_for_dedup(rt) != want:
                        continue
                else:
                    info_n = normalize_program_title_for_dedup(rec_entry.get("info") or "")
                    if not want or want not in info_n:
                        continue
                return {
                    "info": rec_entry["info"],
                    "start_time": rec_entry.get("start_time"),
                    "end_time": rec_entry["end_time"],
                }
            return None

    def cleanup(self):
        """クリーンアップ"""
        self.logger.info("Starting RecorderManager cleanup")
        self.stop_all(wait=True)
        self.logger.info("RecorderManager cleanup completed")

class RecordingSchedule:
    """録音予約"""
    def __init__(self, station_id, station_name, program_title, start_time, end_time, 
                 output_path, filetype="mp3", repeat_type="none", repeat_days=None):
        # 同一開始時刻の予約でも個別に扱えるようIDは常に一意にする
        self.id = f"{station_id}_{start_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.station_id = station_id
        self.station_name = station_name
        self.program_title = program_title
        self.start_time = start_time
        self.end_time = end_time
        self.output_path = output_path
        self.filetype = filetype
        self.repeat_type = repeat_type  # "none", "daily", "weekly"
        self.repeat_days = repeat_days or []  # 週次繰り返しの場合の曜日リスト
        self.last_execution = None
        self.enabled = True
        self.status = RECORDING_STATUS_SCHEDULED  # 初期ステータス

    def to_dict(self):
        """辞書形式に変換"""
        return {
            "id": self.id,
            "station_id": self.station_id,
            "station_name": self.station_name,
            "program_title": self.program_title,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "output_path": self.output_path,
            "filetype": self.filetype,
            "repeat_type": self.repeat_type,
            "repeat_days": self.repeat_days,
            "last_execution": self.last_execution.isoformat() if self.last_execution else None,
            "enabled": self.enabled,
            "status": self.status
        }

    @classmethod
    def from_dict(cls, data):
        """辞書から復元"""
        schedule = cls(
            data["station_id"],
            data["station_name"],
            data["program_title"],
            datetime.datetime.fromisoformat(data["start_time"]),
            datetime.datetime.fromisoformat(data["end_time"]),
            data["output_path"],
            data["filetype"],
            data["repeat_type"],
            data["repeat_days"]
        )
        schedule.id = data["id"]
        schedule.last_execution = datetime.datetime.fromisoformat(data["last_execution"]) if data["last_execution"] else None
        schedule.enabled = data["enabled"]
        schedule.status = data.get("status", RECORDING_STATUS_SCHEDULED)  # 後方互換性のためデフォルト値を設定
        return schedule

    def should_execute(self, current_time):
        """実行すべきかどうかを判定"""
        if not self.enabled:
            return False

        if self.status != RECORDING_STATUS_SCHEDULED:
            return False

        # 前回実行から1分未満なら実行しない
        if self.last_execution and (current_time - self.last_execution).total_seconds() < MIN_RETRY_INTERVAL:
            return False

        # 開始時刻の10秒前から10秒後まで
        time_diff = abs((self.start_time - current_time).total_seconds())
        return time_diff <= SCHEDULE_EXECUTION_WINDOW

    def mark_executed(self, current_time):
        """実行済みとしてマーク"""
        self.last_execution = current_time

    def set_status(self, status):
        """ステータスを更新"""
        self.status = status

    def get_status_display_name(self):
        """ステータスの表示名を取得"""
        status_names = {
            RECORDING_STATUS_SCHEDULED: "予約済み",
            RECORDING_STATUS_RECORDING: "録音中",
            RECORDING_STATUS_COMPLETED: "完了",
            RECORDING_STATUS_CANCELLED: "キャンセル",
            RECORDING_STATUS_FAILED: "失敗"
        }
        return status_names.get(self.status, "不明")

class ScheduleManager:
    """録音予約管理"""
    def __init__(self, recorder_manager, logger=None):
        self.logger = logger or getLogger("schedule_manager")
        self.recorder_manager = recorder_manager
        self.schedules = []
        self.schedule_file = "recording_schedules.json"
        self.timer = None
        self.running = False
        self.lock = threading.Lock()
        self.token_manager = None  # 認証トークン管理
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="schedule_executor")
        self.load_schedules()

    def add_schedule(self, schedule):
        """予約を追加"""
        with self.lock:
            if self._has_duplicate_schedule_locked(schedule):
                self.logger.info(
                    "Duplicate schedule ignored: "
                    f"{schedule.station_id} {schedule.program_title} {schedule.start_time} - {schedule.end_time}"
                )
                return False
            self.schedules.append(schedule)
        self.save_schedules()
        self.logger.info(f"Schedule added: {schedule.program_title}")
        return True

    def _has_duplicate_schedule_locked(self, new_schedule):
        """同一番組・同一時間帯の重複予約を判定（lock取得中専用）"""
        active_statuses = {RECORDING_STATUS_SCHEDULED, RECORDING_STATUS_RECORDING}
        for existing in self.schedules:
            if existing.status not in active_statuses:
                continue
            if (
                existing.station_id == new_schedule.station_id and
                existing.program_title == new_schedule.program_title and
                existing.start_time == new_schedule.start_time and
                existing.end_time == new_schedule.end_time
            ):
                return True
        return False

    def remove_schedule(self, schedule_id):
        """予約エントリを一覧から削除（録音の停止は行わない。キャンセル済み・失敗行の片付けに使用）"""
        with self.lock:
            self.schedules = [s for s in self.schedules if s.id != schedule_id]
        self.save_schedules()
        self.logger.info(f"Schedule removed: {schedule_id}")

    def clear_all_schedules(self):
        """すべての予約を削除"""
        with self.lock:
            # 録音中のスケジュールは録音を停止
            for schedule in list(self.schedules):
                if schedule.status == RECORDING_STATUS_RECORDING:
                    self.recorder_manager.stop_recording_for_program(schedule.station_id, schedule.program_title)
                    schedule.set_status(RECORDING_STATUS_CANCELLED)
                    self.logger.info(f"Cancelled recording schedule: {schedule.program_title}")

            # すべてのスケジュールを削除
            removed_count = len(self.schedules)
            self.schedules.clear()

            # JSONファイルを削除
            try:
                if os.path.exists(self.schedule_file):
                    os.remove(self.schedule_file)
                    self.logger.info(f"Deleted schedule file: {self.schedule_file}")
            except Exception as e:
                self.logger.error(f"Failed to delete schedule file: {e}")
                # ファイル削除に失敗した場合は空のファイルを保存
                self.save_schedules()

            self.logger.info(f"Cleared all schedules: {removed_count} schedules removed")
            return removed_count

    def mark_recording_interrupted(self, rec_entry):
        """録音管理などから手動停止された予約録音をキャンセル済みにする（完了扱いにしない）。"""
        schedule_id = rec_entry.get("schedule_id")
        station_id = rec_entry.get("station_id")
        program_title = rec_entry.get("program_title")
        want = normalize_program_title_for_dedup(program_title) if program_title else ""
        updated = False
        with self.lock:
            for schedule in self.schedules:
                if schedule.status != RECORDING_STATUS_RECORDING:
                    continue
                if schedule_id and schedule.id == schedule_id:
                    schedule.set_status(RECORDING_STATUS_CANCELLED)
                    updated = True
                    break
                if (
                    station_id
                    and schedule.station_id == station_id
                    and want
                    and normalize_program_title_for_dedup(schedule.program_title) == want
                ):
                    schedule.set_status(RECORDING_STATUS_CANCELLED)
                    updated = True
                    break
        if updated:
            self.save_schedules()
            self.logger.info(
                "Schedule marked cancelled after manual recording stop: "
                f"{station_id} {program_title}"
            )

    def cancel_schedule(self, schedule_id):
        """予約をキャンセル（ステータスを更新）。録音中なら該当録音を停止する。"""
        station_id = None
        program_title = None
        was_recording = False
        with self.lock:
            for schedule in self.schedules:
                if schedule.id != schedule_id:
                    continue
                if schedule.status not in (
                    RECORDING_STATUS_SCHEDULED,
                    RECORDING_STATUS_RECORDING,
                ):
                    return False
                was_recording = schedule.status == RECORDING_STATUS_RECORDING
                station_id = schedule.station_id
                program_title = schedule.program_title
                schedule.set_status(RECORDING_STATUS_CANCELLED)
                self.save_schedules()
                self.logger.info(f"Schedule cancelled: {schedule_id}")
                break
            else:
                return False
        if was_recording:
            self.recorder_manager.stop_recording_for_program(station_id, program_title)
        return True

    def reactivate_schedule(self, schedule_id):
        """キャンセル済みで開始時刻前の予約を予約済みに戻す。

        Returns:
            None: 成功
            str: 失敗理由（not_found / not_cancelled / too_late）
        """
        with self.lock:
            for schedule in self.schedules:
                if schedule.id != schedule_id:
                    continue
                if schedule.status != RECORDING_STATUS_CANCELLED:
                    return "not_cancelled"
                now = datetime.datetime.now()
                if now >= schedule.start_time:
                    return "too_late"
                schedule.set_status(RECORDING_STATUS_SCHEDULED)
                self.save_schedules()
                self.logger.info(f"Schedule reactivated: {schedule.program_title} ({schedule_id})")
                return None
        return "not_found"

    def get_schedules(self):
        """予約一覧を取得"""
        with self.lock:
            return self.schedules.copy()

    def count_pending_schedules_for_exit_warning(self):
        """終了確認用。未実行・録音中のみを数え、完了・取消・失敗などの履歴は含めない。"""
        with self.lock:
            return sum(
                1
                for s in self.schedules
                if s.status in (RECORDING_STATUS_SCHEDULED, RECORDING_STATUS_RECORDING)
            )

    def start_monitoring(self):
        """監視を開始"""
        if self.running:
            return
        self.running = True
        self.timer = threading.Thread(target=self._monitor_loop, daemon=True)
        self.timer.start()
        self.logger.info("Schedule monitoring started")

    def stop_monitoring(self):
        """監視を停止"""
        self.running = False
        if self.timer:
            self.timer.join(timeout=5)
        
        # スレッドプールをシャットダウン
        self.executor.shutdown(wait=True)
        
        self.logger.info("Schedule monitoring stopped")

    def _monitor_loop(self):
        """監視ループ"""
        while self.running:
            try:
                current_time = datetime.datetime.now()
                with self.lock:
                    for schedule in self.schedules:
                        if schedule.should_execute(current_time):
                            self._execute_schedule(schedule, current_time)
                time.sleep(SCHEDULE_CHECK_INTERVAL)
            except Exception as e:
                self.logger.error(f"Error in monitor loop: {e}")
                time.sleep(SCHEDULE_CHECK_INTERVAL)

    def _get_authenticated_stream_url(self, station_id, max_retries=3):
        """認証済みのストリームURLを取得（リトライ機能付き）"""
        for attempt in range(max_retries):
            try:
                if not self.token_manager:
                    self.token_manager = token.Token()
                
                # 認証を実行
                auth_response = self.token_manager.auth1()
                partial_key, auth_token = self.token_manager.get_partial_key(auth_response)
                self.token_manager.auth2(partial_key, auth_token)
                
                # 認証済みストリームURLを取得
                base_url = f'http://f-radiko.smartstream.ne.jp/{station_id}/_definst_/simul-stream.stream/playlist.m3u8'
                stream_url = self.token_manager.gen_temp_chunk_m3u8_url(base_url, auth_token)
                
                self.logger.debug(f"Authenticated stream URL obtained: {stream_url}")
                return stream_url
                
            except Exception as e:
                self.logger.warning(f"Authentication attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    # リトライ前に短時間待機（非同期スレッドなので短縮）
                    time.sleep(0.5)
                    # トークンマネージャーをリセット
                    self.token_manager = None
                else:
                    self.logger.error(f"Failed to get authenticated stream URL after {max_retries} attempts: {e}")
                    raise

    def _execute_schedule(self, schedule, current_time):
        """予約を実行（非同期）"""
        self.logger.info(f"Executing schedule: {schedule.program_title}")
        
        # ステータスを録音中に更新
        schedule.set_status(RECORDING_STATUS_RECORDING)
        self.save_schedules()
        
        # 非同期で認証と録音を実行
        def execute_async():
            try:
                with self.lock:
                    entry = next((s for s in self.schedules if s.id == schedule.id), None)
                if entry is None or entry.status != RECORDING_STATUS_RECORDING:
                    self.logger.info(
                        "Skipping scheduled recording (removed or no longer active): "
                        f"{schedule.program_title} ({schedule.id})"
                    )
                    return

                # 認証済みストリームURLの取得
                stream_url = self._get_authenticated_stream_url(schedule.station_id)
                
                # 録音開始
                # ラジコの放送時刻と配信時刻のずれに対応するため、停止時刻を30秒延長
                end_time = time.mktime(schedule.end_time.timetuple()) + RECORDING_END_TIME_BUFFER
                info = f"{schedule.station_name} {schedule.program_title}"
                
                # 録音完了時のコールバック
                def on_recording_complete(recorder):
                    schedule.set_status(RECORDING_STATUS_COMPLETED)
                    self.save_schedules()
                    try:
                        notification_notify(
                            title='録音完了',
                            message=f'{schedule.program_title} の録音が完了しました。',
                            app_name='rpb',
                            timeout=10
                        )
                        self.logger.info(f"Recording completion notification sent successfully: {schedule.program_title}")
                    except Exception as e:
                        self.logger.error(f"Failed to send recording completion notification: {e}")
                
                recorder = self.recorder_manager.start_recording(
                    stream_url, 
                    schedule.output_path, 
                    info, 
                    end_time, 
                    schedule.filetype,
                    on_complete=on_recording_complete,
                    station_id=schedule.station_id,
                    program_title=schedule.program_title,
                    schedule_id=schedule.id,
                )
                
                if recorder:
                    schedule.mark_executed(current_time)
                    self.save_schedules()
                    
                    # 現在のスケジュール数を取得
                    active_schedules = [s for s in self.schedules if s.enabled and s.status == RECORDING_STATUS_RECORDING]
                    schedule_count = len(active_schedules)
                    
                    # 通知メッセージを決定
                    if schedule_count == 1:
                        message = f'{schedule.program_title} の録音を開始しました。'
                    else:
                        message = f'{schedule.program_title} の録音を開始しました。（{schedule_count}件の録音中）'
                    
                    try:
                        notification_notify(
                            title='録音開始',
                            message=message,
                            app_name='rpb',
                            timeout=10
                        )
                        self.logger.info(f"Recording start notification sent successfully: {schedule.program_title}")
                    except Exception as e:
                        self.logger.error(f"Failed to send recording start notification: {e}")
                else:
                    # 録音開始に失敗
                    schedule.set_status(RECORDING_STATUS_FAILED)
                    self.save_schedules()
                    try:
                        notification_notify(
                            title='録音失敗',
                            message=f'{schedule.program_title} の録音開始に失敗しました。',
                            app_name='rpb',
                            timeout=10
                        )
                        self.logger.info(f"Recording failure notification sent successfully: {schedule.program_title}")
                    except Exception as e:
                        self.logger.error(f"Failed to send recording failure notification: {e}")
                
            except Exception as e:
                self.logger.error(f"Failed to execute schedule {schedule.id}: {e}")
                
                # ステータスを失敗に更新
                schedule.set_status(RECORDING_STATUS_FAILED)
                self.save_schedules()
                
                # 認証エラーの場合はユーザーに通知
                if "403" in str(e) or "Forbidden" in str(e) or "access denied" in str(e):
                    try:
                        notification_notify(
                            title='録音失敗',
                            message=f'{schedule.program_title} の録音に失敗しました。認証エラーが発生しました。',
                            app_name='rpb',
                            timeout=10
                        )
                        self.logger.info(f"Recording authentication error notification sent successfully: {schedule.program_title}")
                    except Exception as notify_e:
                        self.logger.error(f"Failed to send recording authentication error notification: {notify_e}")
                else:
                    try:
                        notification_notify(
                            title='録音失敗',
                            message=f'{schedule.program_title} の録音に失敗しました。エラー: {str(e)[:100]}',
                            app_name='rpb',
                            timeout=10
                        )
                        self.logger.info(f"Recording error notification sent successfully: {schedule.program_title}")
                    except Exception as notify_e:
                        self.logger.error(f"Failed to send recording error notification: {notify_e}")
        
        # スレッドプールで実行
        self.executor.submit(execute_async)

    def save_schedules(self):
        """予約をファイルに保存"""
        try:
            with open(self.schedule_file, 'w', encoding='utf-8') as f:
                json.dump([s.to_dict() for s in self.schedules], f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save schedules: {e}")

    def load_schedules(self):
        """予約をファイルから読み込み"""
        try:
            if os.path.exists(self.schedule_file):
                with open(self.schedule_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.schedules = [RecordingSchedule.from_dict(item) for item in data]
                self.logger.info(f"Loaded {len(self.schedules)} schedules")
        except Exception as e:
            self.logger.error(f"Failed to load schedules: {e}")
            self.schedules = []

    def cleanup(self):
        """アプリ終了時のクリーンアップ処理"""
        try:
            self.logger.info("Starting schedule cleanup...")
            
            # 監視を停止
            self.stop_monitoring()
            
            # 録音中のスケジュールをキャンセル状態に更新
            with self.lock:
                updated_count = 0
                for schedule in self.schedules:
                    if schedule.status == RECORDING_STATUS_RECORDING:
                        schedule.set_status(RECORDING_STATUS_CANCELLED)
                        updated_count += 1
                        self.logger.info(f"Cancelled recording schedule: {schedule.program_title}")
                
                if updated_count > 0:
                    self.save_schedules()
                    self.logger.info(f"Updated {updated_count} recording schedules to cancelled status")
            
            # スレッドプールをシャットダウン
            if self.executor:
                self.executor.shutdown(wait=False)
                self.logger.info("Schedule executor shutdown completed")
            
            self.logger.info("Schedule cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during schedule cleanup: {e}")

    def cleanup_on_error(self):
        """異常終了時のクリーンアップ処理"""
        try:
            self.logger.warning("Starting emergency schedule cleanup...")
            
            # 録音中のスケジュールを失敗状態に更新
            with self.lock:
                updated_count = 0
                for schedule in self.schedules:
                    if schedule.status == RECORDING_STATUS_RECORDING:
                        schedule.set_status(RECORDING_STATUS_FAILED)
                        updated_count += 1
                        self.logger.warning(f"Marked recording schedule as failed: {schedule.program_title}")
                
                if updated_count > 0:
                    self.save_schedules()
                    self.logger.warning(f"Updated {updated_count} recording schedules to failed status")
            
            self.logger.warning("Emergency schedule cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during emergency schedule cleanup: {e}")

# グローバルインスタンス
recorder_manager = RecorderManager()
schedule_manager = ScheduleManager(recorder_manager)

# 終了時のクリーンアップ
atexit.register(recorder_manager.cleanup)
atexit.register(schedule_manager.cleanup)

# シグナルハンドラー（WindowsではSIGTERMとSIGINTのみ）
def signal_handler(signum, frame):
    """シグナル受信時のクリーンアップ処理"""
    try:
        print(f"Received signal {signum}, cleaning up...")
        schedule_manager.cleanup_on_error()
        recorder_manager.cleanup()
        from views.mpvPlayer import shutdown_global_mpv_player
        shutdown_global_mpv_player()
    except Exception as e:
        print(f"Error during signal cleanup: {e}")
    finally:
        os._exit(1)

# シグナルハンドラーを登録
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# 後方互換性のためのヘルパー関数
def create_recording_dir(station_id, program_title=None):
    """放送局名のディレクトリを作成（後方互換性）"""
    # 設定から出力先フォルダを取得
    base_dir = get_output_directory()
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    
    # 放送局ディレクトリを作成
    station_dir = os.path.join(base_dir, station_id)
    if not os.path.exists(station_dir):
        os.makedirs(station_dir)
    
    # 番組ごとのサブフォルダ作成設定をチェック
    if program_title and get_create_station_subdir_setting():
        # 番組タイトルをファイル名に適した形式に変換
        safe_program_title = re.sub(r'[<>:"/\\|?*]', '_', program_title)
        safe_program_title = safe_program_title.strip()
        
        # 放送局\番組タイトルのディレクトリを作成
        program_dir = os.path.join(station_dir, safe_program_title)
        if not os.path.exists(program_dir):
            os.makedirs(program_dir)
        return program_dir
    
    return station_dir

def get_output_directory():
    """設定から録音出力先ディレクトリを取得"""
    try:
        # globalVarsから設定を取得
        if hasattr(globalVars, 'app') and hasattr(globalVars.app, 'config'):
            output_dir = globalVars.app.config.getstring("record", "output_directory", "OUTPUT")
            
            # 相対パスの場合は絶対パスに変換
            if not os.path.isabs(output_dir):
                output_dir = os.path.abspath(output_dir)
            
            # ディレクトリが存在しない場合は作成を試行
            if not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir, exist_ok=True)
                except (PermissionError, OSError) as e:
                    logger = getLogger("recorder")
                    logger.error(f"Failed to create output directory {output_dir}: {e}")
                    # 作成に失敗した場合はデフォルトディレクトリを使用
                    return "OUTPUT"
            
            return output_dir
        else:
            # フォールバック: デフォルト値を使用
            return "OUTPUT"
    except Exception as e:
        # エラーが発生した場合はデフォルト値を使用
        logger = getLogger("recorder")
        logger.warning(f"Failed to get output directory from config: {e}, using default")
        return "OUTPUT"

def get_create_station_subdir_setting():
    """番組ごとのサブフォルダ作成設定を取得"""
    try:
        # globalVarsから設定を取得
        if hasattr(globalVars, 'app') and hasattr(globalVars.app, 'config'):
            return globalVars.app.config.getboolean("record", "createStationSubDir", True)
        else:
            # フォールバック: デフォルト値を使用
            return True
    except Exception as e:
        # エラーが発生した場合はデフォルト値を使用
        logger = getLogger("recorder")
        logger.warning(f"Failed to get createStationSubDir setting: {e}, using default")
        return True

def get_file_type_from_config():
    """設定からファイルタイプを取得（後方互換性）"""
    try:
        config = ConfigManager.ConfigManager()
        menu_id = config.getint("record", "menu_id")
        if menu_id == constants.RECORDING_MP3:  # MP3
            return "mp3"
        elif menu_id == constants.RECORDING_WAV:  # WAV
            return "wav"
        elif menu_id == constants.RECORDING_M4A:  # M4A
            return "m4a"
        else:
            # デフォルトはMP3
            return "mp3"
    except Exception as e:
        # 設定が存在しない場合はデフォルトでMP3
        return "mp3"

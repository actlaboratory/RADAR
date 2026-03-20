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
from collections import deque
from accessible_output2.outputs.base import OutputError
from concurrent.futures import ThreadPoolExecutor
import queue

import simpleDialog


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
RECORDING_END_TIME_BUFFER = 30  # 秒（ラジコの放送時刻と配信時刻のずれに対応するため、停止時刻を延長）

# 録音ステータス定数
RECORDING_STATUS_SCHEDULED = "scheduled"  # 予約スケジュール済み
RECORDING_STATUS_RECORDING = "recording"  # 録音中
RECORDING_STATUS_COMPLETED = "completed"  # 録音が正しく完了している
RECORDING_STATUS_CANCELLED = "cancelled"  # ユーザーによってキャンセルされた
RECORDING_STATUS_FAILED = "failed"  # 予約録音がエラーによって失敗した

class RecorderError(Exception):
    """録音関連のエラー"""
    pass

class Recorder:
    """
    レコーダー: 指定URLのストリームを指定パスに保存。エラー時はコールバックで管理者に通知。
    """
    def __init__(self, stream_url, output_path, filetype, on_error=None, logger=None, recording_seconds=None, http_headers=None, input_options=None):
        self.stream_url = stream_url
        self.output_path = output_path
        self.filetype = filetype
        self.on_error = on_error
        self.logger = logger or getLogger("recorder")
        self.process = None
        self.recording = False
        self._stop_event = threading.Event()
        self.last_ffmpeg_cmd = ""
        self._stderr_lines = deque(maxlen=80)
        self._stderr_lock = threading.Lock()
        self.recording_seconds = recording_seconds
        self.http_headers = dict(http_headers or {})
        self.input_options = list(input_options or [])

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
                import subprocess
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            
            self.process = subprocess.Popen(
                cmd, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self.recording = True
            self.logger.info(f"FFmpeg process started with PID: {self.process.pid}")
            threading.Thread(target=self._consume_stderr, daemon=True).start()
            threading.Thread(target=self._monitor, daemon=True).start()
        except Exception as e:
            self.logger.error(f"Failed to start recording: {e}")
            self._notify_error(e)
            raise  # 例外を再投げしてRecorderManagerでキャッチできるようにする

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
        """ffmpegのstderrを継続的に読み取り、最後の数行を保持"""
        proc = self.process
        if not proc or not proc.stderr:
            return
        try:
            while True:
                line = proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="ignore").rstrip()
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

    def start_recording(self, stream_url, output_path, info, end_time, filetype="mp3", on_complete=None, station_id=None, program_title=None, recording_seconds=None, http_headers=None, input_options=None):
        """録音を開始"""
        try:
            self.last_start_error = ""
            def on_error(rec, error):
                self._handle_error(rec, error, info, stream_url, output_path, end_time, filetype, recording_seconds, http_headers, input_options)
            
            recorder = Recorder(
                stream_url,
                output_path,
                filetype,
                on_error=on_error,
                logger=self.logger,
                recording_seconds=recording_seconds,
                http_headers=http_headers,
                input_options=input_options
            )
            with self.lock:
                self.recorders.append({
                    "recorder": recorder,
                    "info": info,
                    "retry_count": 0,
                    "end_time": end_time,
                    "on_complete": on_complete,
                    "station_id": station_id,
                    "program_title": program_title,
                    "start_time": time.time(),
                    "recording_seconds": recording_seconds,
                    "http_headers": dict(http_headers or {}),
                    "input_options": list(input_options or [])
                })
            recorder.start()
            # 終了タイマー
            threading.Thread(target=self._schedule_stop, args=(recorder, end_time, on_complete), daemon=True).start()
            self.logger.info(f"Recorder started: {info}")
            return recorder
        except Exception as e:
            self.last_start_error = str(e)
            self.logger.error(f"Failed to start recording: {e}")
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
            else:
                self.logger.error(f"Recording failed after {MAX_RETRY} attempts: {info}")
                try:
                    notification_notify(title="録音失敗", message=f"{info} の録音に失敗しました。", app_name="rpb", timeout=10)
                    self.logger.info(f"Recording failure notification sent successfully after max retries: {info}")
                except Exception as e:
                    self.logger.error(f"Failed to send recording failure notification after max retries: {e}")
        # start_recording は lock の外で実行（デッドロック防止）
        if should_retry:
            self.start_recording(
                stream_url,
                retry_path,
                info,
                end_time,
                filetype,
                on_complete,
                recording_seconds=recording_seconds,
                http_headers=http_headers,
                input_options=input_options
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

    def stop_all(self):
        """全ての録音を停止"""
        with self.lock:
            active_count = len(self.recorders)
            self.logger.info(f"Stopping all {active_count} active recorders")
            
            # 停止処理を並列で実行
            stop_threads = []
            for rec_entry in self.recorders:
                def stop_recorder(rec):
                    try:
                        rec.stop()
                        self.logger.info(f"Recorder stopped successfully: {rec_entry['info']}")
                    except Exception as e:
                        self.logger.error(f"Error stopping recorder: {e}")
                
                thread = threading.Thread(target=stop_recorder, args=(rec_entry["recorder"],), daemon=True)
                thread.start()
                stop_threads.append(thread)
            
            # 全ての停止スレッドの完了を待つ（最大10秒）
            for thread in stop_threads:
                thread.join(timeout=10)
            
            self.recorders.clear()
            self.logger.info(f"All {active_count} recorders stopped and instances cleared.")

    def stop_recorder(self, recorder):
        """指定された録音を停止"""
        with self.lock:
            for i, rec_entry in enumerate(self.recorders):
                if rec_entry["recorder"] == recorder:
                    self.logger.info(f"Stopping specific recorder: {rec_entry['info']}")
                    rec_entry["recorder"].stop()
                    del self.recorders[i]
                    self.logger.info(f"Recorder stopped and removed from manager: {rec_entry['info']}")
                    break

    def get_active_recorders(self):
        """アクティブな録音の一覧を取得"""
        with self.lock:
            return [(r["recorder"], r["info"]) for r in self.recorders if self._is_recorder_active(r["recorder"])]

    def get_station_recorders(self, station_id):
        """指定された放送局の録音一覧を取得"""
        with self.lock:
            return [r for r in self.recorders if station_id in r["info"] and self._is_recorder_active(r["recorder"])]

    def is_station_recording(self, station_id):
        """指定された放送局が録音中かどうかを判定"""
        with self.lock:
            return any(station_id in r["info"] and self._is_recorder_active(r["recorder"]) for r in self.recorders)

    def stop_station_recording(self, station_id):
        """指定された放送局の録音を停止"""
        with self.lock:
            stopped_count = 0
            # 後ろから削除することでインデックスの問題を回避
            for i in range(len(self.recorders) - 1, -1, -1):
                rec_entry = self.recorders[i]
                if station_id in rec_entry["info"] and self._is_recorder_active(rec_entry["recorder"]):
                    rec_entry["recorder"].stop()
                    del self.recorders[i]
                    stopped_count += 1
                    self.logger.info(f"Stopped recording for station {station_id}: {rec_entry['info']}")
            return stopped_count

    def is_duplicate_recording(self, station_id, program_title):
        """同じ放送局・同じ番組の重複録音をチェック"""
        with self.lock:
            for rec_entry in self.recorders:
                if (self._is_recorder_active(rec_entry["recorder"]) and 
                    rec_entry.get("station_id") == station_id and 
                    rec_entry.get("program_title") == program_title):
                    return True
            return False

    def get_recording_info(self, station_id, program_title):
        """指定された放送局・番組の録音情報を取得"""
        with self.lock:
            for rec_entry in self.recorders:
                if (self._is_recorder_active(rec_entry["recorder"]) and 
                    rec_entry.get("station_id") == station_id and 
                    rec_entry.get("program_title") == program_title):
                    return {
                        "info": rec_entry["info"],
                        "start_time": rec_entry.get("start_time"),
                        "end_time": rec_entry["end_time"]
                    }
            return None

    def cleanup(self):
        """クリーンアップ"""
        self.logger.info("Starting RecorderManager cleanup")
        self.stop_all()
        self.logger.info("RecorderManager cleanup completed")

class RecordingSchedule:
    """録音予約"""
    def __init__(self, station_id, station_name, program_title, start_time, end_time, 
                 output_path, filetype="mp3", repeat_type="none", repeat_days=None):
        self.id = f"{station_id}_{start_time.strftime('%Y%m%d_%H%M%S')}"
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
            self.schedules.append(schedule)
        self.save_schedules()
        self.logger.info(f"Schedule added: {schedule.program_title}")

    def remove_schedule(self, schedule_id):
        """予約を削除"""
        with self.lock:
            self.schedules = [s for s in self.schedules if s.id != schedule_id]
        self.save_schedules()
        self.logger.info(f"Schedule removed: {schedule_id}")

    def clear_all_schedules(self):
        """すべての予約を削除"""
        with self.lock:
            # 録音中のスケジュールをキャンセル
            for schedule in self.schedules:
                if schedule.status == RECORDING_STATUS_RECORDING:
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

    def cancel_schedule(self, schedule_id):
        """予約をキャンセル（ステータスを更新）"""
        with self.lock:
            for schedule in self.schedules:
                if schedule.id == schedule_id:
                    schedule.set_status(RECORDING_STATUS_CANCELLED)
                    self.save_schedules()
                    self.logger.info(f"Schedule cancelled: {schedule_id}")
                    return True
        return False

    def get_schedules(self):
        """予約一覧を取得"""
        with self.lock:
            return self.schedules.copy()

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
                    program_title=schedule.program_title
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

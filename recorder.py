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
from plyer import notification
from logging import getLogger
from views import token
import threading
import time
import json
import datetime
from accessible_output2.outputs.base import OutputError

import simpleDialog

# ロケール設定を修正
try:
    locale.setlocale(locale.LC_TIME, 'Japanese_Japan.932')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'ja_JP.UTF-8')
    except locale.Error:
        try:
            locale.setlocale(locale.LC_TIME, 'C')
        except locale.Error:
            pass  # デフォルトのまま

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

class RecorderError(Exception):
    """録音関連のエラー"""
    pass

class Recorder:
    """
    レコーダー: 指定URLのストリームを指定パスに保存。エラー時はコールバックで管理者に通知。
    """
    def __init__(self, stream_url, output_path, filetype, on_error=None, logger=None):
        self.stream_url = stream_url
        self.output_path = output_path
        self.filetype = filetype
        self.on_error = on_error
        self.logger = logger or getLogger("recorder")
        self.process = None
        self.recording = False
        self._stop_event = threading.Event()

    def start(self):
        """録音を開始"""
        try:
            self.logger.info(f"Start recording: {self.stream_url} -> {self.output_path}.{self.filetype}")
            ffmpeg_path = self._get_ffmpeg_path()
            cmd = [
                ffmpeg_path,
                "-loglevel", "error",
                "-i", self.stream_url,
                "-f", self.filetype,
                "-ac", "2",
                "-vn", f"{self.output_path}.{self.filetype}",
                "-y"
            ]
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.recording = True
            threading.Thread(target=self._monitor, daemon=True).start()
        except Exception as e:
            self.logger.error(f"Failed to start recording: {e}")
            self._notify_error(e)

    def stop(self):
        """録音を安全に停止"""
        self.logger.info(f"Stop requested for: {self.output_path}.{self.filetype}")
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.logger.warning("Terminate failed, killing process")
                try:
                    self.process.kill()
                except Exception as e:
                    self.logger.error(f"Kill failed: {e}")
        self.recording = False
        self.process = None
        self.logger.info(f"Recording stopped: {self.output_path}.{self.filetype}")

    def _monitor(self):
        """録音プロセスの監視"""
        try:
            while not self._stop_event.is_set():
                if self.process.poll() is not None:
                    # プロセスが異常終了
                    stderr = self.process.stderr.read().decode(errors="ignore") if self.process.stderr else ""
                    raise RecorderError(f"Recording process exited unexpectedly: {stderr}")
                time.sleep(1)
        except Exception as e:
            self.logger.error(f"Monitor error: {e}")
            self._notify_error(e)
        finally:
            self.recording = False

    def _notify_error(self, error):
        """エラーを管理者に通知"""
        if self.on_error:
            self.on_error(self, error)

    def _get_ffmpeg_path(self):
        """ffmpegのパスを取得"""
        ffmpeg_path = os.path.join(os.getcwd(), "bin", "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            raise RecorderError("ffmpeg.exe not found.")
        return ffmpeg_path

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

    def start_recording(self, stream_url, output_path, info, end_time, filetype="mp3"):
        """録音を開始"""
        def on_error(rec, error):
            self._handle_error(rec, error, info, stream_url, output_path, end_time, filetype)
        
        recorder = Recorder(stream_url, output_path, filetype, on_error=on_error, logger=self.logger)
        with self.lock:
            self.recorders.append({
                "recorder": recorder,
                "info": info,
                "retry_count": 0,
                "end_time": end_time
            })
        recorder.start()
        # 終了タイマー
        threading.Thread(target=self._schedule_stop, args=(recorder, end_time), daemon=True).start()
        self.logger.info(f"Recorder started: {info}")
        return recorder

    def _schedule_stop(self, recorder, end_time):
        """指定時刻に録音を停止"""
        now = time.time()
        wait = max(0, end_time - now)
        max_wait = self.max_hours * 3600
        wait = min(wait, max_wait)
        self.logger.debug(f"Recorder will stop in {wait} seconds.")
        time.sleep(wait)
        recorder.stop()
        self.logger.info(f"Recorder stopped by schedule: {recorder.output_path}")

    def _handle_error(self, recorder, error, info, stream_url, output_path, end_time, filetype):
        """エラー処理とリトライ"""
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
                
            if retry < MAX_RETRY:
                self.logger.info(f"Retrying recording: {new_path}")
                self.start_recording(stream_url, new_path, info, end_time, filetype)
            else:
                self.logger.error(f"Recording failed after {MAX_RETRY} attempts: {info}")
                notification.notify(title="録音失敗", message=f"{info} の録音に失敗しました。", app_name="rpb", timeout=10)

    def stop_all(self):
        """全ての録音を停止"""
        with self.lock:
            for rec_entry in self.recorders:
                rec_entry["recorder"].stop()
            self.recorders.clear()
        self.logger.info("All recorders stopped.")

    def stop_recorder(self, recorder):
        """指定された録音を停止"""
        with self.lock:
            for rec_entry in self.recorders:
                if rec_entry["recorder"] == recorder:
                    rec_entry["recorder"].stop()
                    self.recorders.remove(rec_entry)
                    self.logger.info(f"Recorder stopped: {rec_entry['info']}")
                    break

    def get_active_recorders(self):
        """アクティブな録音の一覧を取得"""
        with self.lock:
            return [(r["recorder"], r["info"]) for r in self.recorders if r["recorder"].is_recording()]

    def cleanup(self):
        """クリーンアップ"""
        self.stop_all()

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
            "enabled": self.enabled
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

    def _execute_schedule(self, schedule, current_time):
        """予約を実行"""
        try:
            self.logger.info(f"Executing schedule: {schedule.program_title}")
            
            # ストリームURLの取得（実際の実装では適切な方法で取得）
            stream_url = f'http://f-radiko.smartstream.ne.jp/{schedule.station_id}/_definst_/simul-stream.stream/playlist.m3u8'
            
            # 録音開始
            end_time = time.mktime(schedule.end_time.timetuple())
            info = f"{schedule.station_name} {schedule.program_title}"
            
            self.recorder_manager.start_recording(
                stream_url, 
                schedule.output_path, 
                info, 
                end_time, 
                schedule.filetype
            )
            
            schedule.mark_executed(current_time)
            self.save_schedules()
            
            notification.notify(
                title='録音開始',
                message=f'{schedule.program_title} の録音を開始しました。',
                app_name='rpb',
                timeout=10
            )
            
        except Exception as e:
            self.logger.error(f"Failed to execute schedule {schedule.id}: {e}")

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

# グローバルインスタンス
recorder_manager = RecorderManager()
schedule_manager = ScheduleManager(recorder_manager)

# 終了時のクリーンアップ
atexit.register(recorder_manager.cleanup)
atexit.register(schedule_manager.stop_monitoring)

# 後方互換性のための古いRecorderクラス
class LegacyRecorder:
    """後方互換性のための古いRecorderクラス"""
    def __init__(self):
        self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "recorder"))
        self.config = ConfigManager.ConfigManager()

        # outputフォルダの存在をチェックして、なかったら作る
        self.BASE_RECORDING_DIR = "OUTPUT"
        if not os.path.exists(self.BASE_RECORDING_DIR):
            os.makedirs(self.BASE_RECORDING_DIR)
            self.log.info("created baseRecordingDirectory")

        self.filetypes = ["mp3", "wav"]
        self.ftp = "mp3"
        self.code = None
        self.recording = False
        self.path = None

        # 終了時にプロセスを安全に終了するためのハンドラーを設定
        atexit.register(self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)
        signal.signal(signal.SIGINT, self.cleanup)

    def setFileType(self, index):
        """録音音質を決定"""
        if index > 0:
            self.ftp = self.filetypes[index-10000]
        else:
            self.ftp = self.filetypes[index]
            globalVars.app.config["recording"]["menu_id"] = index+10000
        self.log.info(f"File type determined: {self.ftp}")

    def record(self, streamUrl, path):
        """ffmpegを用いて録音（後方互換性）"""
        if not os.path.exists(constants.FFMPEG_PATH):
            simpleDialog.errorDialog(_("録音を開始できませんでした。ffmpeg.exeが見つかりません。"))
            self.log.error("'ffmpeg.exe' not found.")
            return False
            
        self.path = path
        logLevel = globalVars.app.config.getint("general", "log_level")
        selected_log_mode = logLevelSelection[str(logLevel)]
        
        try:
            ffmpeg_setting = [
                constants.FFMPEG_PATH,
                "-loglevel", selected_log_mode,
                "-i", streamUrl,
                "-f", self.ftp,
                "-ac", "2",
                "-vn", f"{path}.{self.ftp}",
                "-y"
            ]
            if selected_log_mode != "quiet":
                log_file = open(os.path.join(os.getcwd(), constants.FFMPEG_LOG_FILE), "w")
                self.code = subprocess.Popen(ffmpeg_setting, stdin=subprocess.PIPE, stdout=log_file, stderr=log_file)
            else:
                self.code = subprocess.Popen(ffmpeg_setting, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.recording = True
            return True
        except Exception as e:
            self.log.error(f"Recording failed: {e}")
            return False

    def stop_record(self, called_from_destructor=False):
        """録音を終了（後方互換性）"""
        self.log.debug("Recording stop requested")
        
        if not self.code:
            self.log.debug("No active recording process found")
            self.recording = False
            return

        try:
            if self.code.poll() is None:
                self.log.debug("Active recording process found, attempting to stop")
                try:
                    self.code.stdin.close()
                except Exception as e:
                    self.log.error(f"Error closing stdin: {e}")

                self.code.terminate()
                
                try:
                    self.code.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.log.warning("Process didn't terminate gracefully, forcing kill")
                    self.code.kill()
                    try:
                        self.code.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.log.error("Failed to kill process even after force kill")
            else:
                self.log.debug("Recording process has already ended")

            if not called_from_destructor:
                notification.notify(
                    title='録音完了',
                    message=f'ファイルは正しく{self.path}として保存されました。',
                    app_name='rpb',
                    timeout=10
                )

        except Exception as e:
            self.log.error(f"Error during recording stop: {e}")
        finally:
            self.recording = False
            self.code = None
            self.log.debug("Recording cleanup completed")

    def cleanup(self, *args):
        """プロセスを安全に終了"""
        if self.recording:
            self.log.debug("Cleanup requested while recording is active")
            self.stop_record(called_from_destructor=True)
        else:
            self.log.debug("Cleanup requested but no active recording")

    def create_recordingDir(self, stationid):
        """放送局名のディレクトリを作成"""
        try:
            dir_path = os.path.join(self.BASE_RECORDING_DIR, stationid)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                self.log.debug("Directory created: " + dir_path)
        except Exception as e:
            self.log.error(f"Failed to create directory: {e}")

        return dir_path

    def __del__(self):
        if self.recording:
            self.stop_record()
            self.log.info("Emergency recording stop completed during instance destruction")
        self.log.debug("deleted")

# 後方互換性のためのエイリアス
Recorder = LegacyRecorder
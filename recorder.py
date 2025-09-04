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
from concurrent.futures import ThreadPoolExecutor
import queue

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
            raise  # 例外を再投げしてRecorderManagerでキャッチできるようにする

    def stop(self):
        """録音を安全に停止"""
        self.logger.info(f"Stop requested for: {self.output_path}.{self.filetype}")
        self._stop_event.set()
        
        if self.process and self.process.poll() is None:
            try:
                # まずstdinを閉じる（ffmpegに終了シグナルを送る）
                if self.process.stdin:
                    self.process.stdin.close()
                
                # プロセスを終了
                self.process.terminate()
                
                # 終了を待つ
                try:
                    self.process.wait(timeout=5)
                    self.logger.debug("Process terminated gracefully")
                except subprocess.TimeoutExpired:
                    self.logger.warning("Terminate failed, killing process")
                    try:
                        self.process.kill()
                        self.process.wait(timeout=2)
                        self.logger.debug("Process killed forcefully")
                    except subprocess.TimeoutExpired:
                        self.logger.error("Failed to kill process even after force kill")
                    except Exception as e:
                        self.logger.error(f"Kill failed: {e}")
                        
            except Exception as e:
                self.logger.error(f"Error during process termination: {e}")
        
        # 状態をリセット
        self.recording = False
        self.process = None
        self.logger.info(f"Recording stopped: {self.output_path}.{self.filetype}")

    def _monitor(self):
        """録音プロセスの監視"""
        try:
            while not self._stop_event.is_set():
                if self.process.poll() is not None:
                    # プロセスが終了
                    if self._stop_event.is_set():
                        # 正常終了（stop()が呼ばれた場合）
                        self.logger.debug("Recording process stopped normally")
                        break
                    else:
                        # 異常終了
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

    def start_recording(self, stream_url, output_path, info, end_time, filetype="mp3", on_complete=None):
        """録音を開始"""
        try:
            def on_error(rec, error):
                self._handle_error(rec, error, info, stream_url, output_path, end_time, filetype)
            
            recorder = Recorder(stream_url, output_path, filetype, on_error=on_error, logger=self.logger)
            with self.lock:
                self.recorders.append({
                    "recorder": recorder,
                    "info": info,
                    "retry_count": 0,
                    "end_time": end_time,
                    "on_complete": on_complete
                })
            recorder.start()
            # 終了タイマー
            threading.Thread(target=self._schedule_stop, args=(recorder, end_time, on_complete), daemon=True).start()
            self.logger.info(f"Recorder started: {info}")
            return recorder
        except Exception as e:
            self.logger.error(f"Failed to start recording: {e}")
            return None

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
        
        # 録音完了コールバックを呼び出し
        if on_complete:
            on_complete(recorder)

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
                # 元のコールバックを保持してリトライ
                on_complete = rec_entry.get("on_complete")
                self.start_recording(stream_url, new_path, info, end_time, filetype, on_complete)
            else:
                self.logger.error(f"Recording failed after {MAX_RETRY} attempts: {info}")
                notification.notify(title="録音失敗", message=f"{info} の録音に失敗しました。", app_name="rpb", timeout=10)

    def stop_all(self):
        """全ての録音を停止"""
        with self.lock:
            # 停止処理を並列で実行
            stop_threads = []
            for rec_entry in self.recorders:
                def stop_recorder(rec):
                    try:
                        rec.stop()
                    except Exception as e:
                        self.logger.error(f"Error stopping recorder: {e}")
                
                thread = threading.Thread(target=stop_recorder, args=(rec_entry["recorder"],), daemon=True)
                thread.start()
                stop_threads.append(thread)
            
            # 全ての停止スレッドの完了を待つ（最大10秒）
            for thread in stop_threads:
                thread.join(timeout=10)
            
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
        self.executor.shutdown(wait=True, timeout=10)
        
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
                end_time = time.mktime(schedule.end_time.timetuple())
                info = f"{schedule.station_name} {schedule.program_title}"
                
                # 録音完了時のコールバック
                def on_recording_complete(recorder):
                    schedule.set_status(RECORDING_STATUS_COMPLETED)
                    self.save_schedules()
                    notification.notify(
                        title='録音完了',
                        message=f'{schedule.program_title} の録音が完了しました。',
                        app_name='rpb',
                        timeout=10
                    )
                
                recorder = self.recorder_manager.start_recording(
                    stream_url, 
                    schedule.output_path, 
                    info, 
                    end_time, 
                    schedule.filetype,
                    on_complete=on_recording_complete
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
                    
                    notification.notify(
                        title='録音開始',
                        message=message,
                        app_name='rpb',
                        timeout=10
                    )
                else:
                    # 録音開始に失敗
                    schedule.set_status(RECORDING_STATUS_FAILED)
                    self.save_schedules()
                    notification.notify(
                        title='録音失敗',
                        message=f'{schedule.program_title} の録音開始に失敗しました。',
                        app_name='rpb',
                        timeout=10
                    )
                
            except Exception as e:
                self.logger.error(f"Failed to execute schedule {schedule.id}: {e}")
                
                # ステータスを失敗に更新
                schedule.set_status(RECORDING_STATUS_FAILED)
                self.save_schedules()
                
                # 認証エラーの場合はユーザーに通知
                if "403" in str(e) or "Forbidden" in str(e) or "access denied" in str(e):
                    notification.notify(
                        title='録音失敗',
                        message=f'{schedule.program_title} の録音に失敗しました。認証エラーが発生しました。',
                        app_name='rpb',
                        timeout=10
                    )
                else:
                    notification.notify(
                        title='録音失敗',
                        message=f'{schedule.program_title} の録音に失敗しました。エラー: {str(e)[:100]}',
                        app_name='rpb',
                        timeout=10
                    )
        
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
def create_recording_dir(station_id):
    """放送局名のディレクトリを作成（後方互換性）"""
    base_dir = "OUTPUT"
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    
    dir_path = os.path.join(base_dir, station_id)
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    
    return dir_path

def get_file_type_from_config():
    """設定からファイルタイプを取得（後方互換性）"""
    filetypes = ["mp3", "wav"]
    try:
        config = ConfigManager.ConfigManager()
        menu_id = config.getint("recording", "menu_id")
        if menu_id > 0:
            return filetypes[menu_id-10000]
        else:
            return filetypes[menu_id]
    except:
        return "mp3"

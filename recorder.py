#recording module
import ConfigManager
import sys
import os
import globalVars
import subprocess
import constants
import atexit
import signal
from plyer import notification
from logging import getLogger
from views import token

import simpleDialog

logLevelSelection = {
    "50":"fatal",
    "40":"error",
    "30":"warning",
    "20":"info",
    "10":"debug",
    "0":"quiet"
}
recording = False

class Recorder:
    def __init__(self):
        self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "recorder"))
        self.config = ConfigManager.ConfigManager()

        # outputフォルダの存在をチェックして、なかったら作る
        self.BASE_RECORDING_DIR = "OUTPUT"
        if not os.path.exists(self.BASE_RECORDING_DIR):
            os.makedirs(self.BASE_RECORDING_DIR)
            self.log.info("created baseRecordingDirectory")

        self.filetypes = [
            "mp3",
            "wav",
        ]
        self.code = None
        self.recording = False

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
        """ffmpegを用いて録音"""
        #ffmpegの存在確認
        if not os.path.exists(constants.FFMPEG_PATH):
            simpleDialog.errorDialog(_("録音を開始できませんでした。ffmpeg.exeが見つかりません。\nこの問題が引き続き発生する場合は、お手数ですがソフトウェアをダウンロードし直してからサイド実行してください。それでも改善しない場合は、開発者までご連絡ください。"))
            self.log.error("'ffmpeg.exe' not found.")
            return False
        self.path = path
        logLevel =  globalVars.app.config.getint("general", "log_level")
        selected_log_mode = logLevelSelection[str(logLevel)]
        self.log.debug(f"ffmpegLogMode:{selected_log_mode}")
        self.log.debug(f"streamUrl: {streamUrl} output: {self.path}")
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
                # ログファイルの設定
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
        """録音を終了"""
        self.log.debug("Recording stop requested")
        
        if not self.code:
            self.log.debug("No active recording process found")
            self.recording = False
            return

        try:
            # プロセスが実行中かチェック
            if self.code.poll() is None:
                self.log.debug("Active recording process found, attempting to stop")
                # 標準入力を閉じる
                try:
                    self.code.stdin.close()
                except Exception as e:
                    self.log.error(f"Error closing stdin: {e}")

                # プロセスを終了
                self.code.terminate()
                
                # 終了を待機
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

            # 通知を表示
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
        if self.recording:  # 録音中の場合は停止を試みる
            self.stop_record()
            self.log.info("Emergency recording stop completed during instance destruction")
        self.log.debug("deleted")
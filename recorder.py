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
now_record = False



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

        # 終了時にプロセスを安全に終了するためのハンドラーを設定
        atexit.register(self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)
        signal.signal(signal.SIGINT, self.cleanup)

    def setFileType(self, index):
        """録音音質を決定"""
        self.ftp = self.filetypes[index]
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
        except Exception as e:
            self.log.error(f"Recording failed: {e}")
        globalVars.app.hMainView.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("録音を停止(&T)"))
        globalVars.app.hMainView.menu.EnableMenu("RECORDING_SCHEDULE", False)
        now_record = True
        return True

    def stop_record(self):
        """録音を終了"""
        self.log.debug("recording stopped!")
        if self.code:
            self.cleanup()
        globalVars.app.hMainView.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("今すぐ録音(&R)"))
        now_record = False
        globalVars.app.hMainView.menu.EnableMenu("RECORDING_SCHEDULE", True)

    def cleanup(self, *args):
        """プロセスを安全に終了"""
        if self.code and self.code.poll() is None:
            self.log.debug("Cleaning up recording process")
            try:
                self.code.stdin.close()
                self.code.terminate()
                self.code.wait()
                notification.notify(
                    title='録音完了',
                    message=f'ファイルは正しく{self.path}として保存されました。',
                    app_name='rpb',
                    timeout=10
                )
            except Exception as e:
                self.log.error(f"Failed to stop recording: {e}")

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

#rpb radio recording module

import sys
import os
import subprocess
import constants
from plyer import notification
from views import token
from logging import getLogger
import ConfigManager
import simpleDialog
import re

class Recorder:
    def __init__(self):
        """コンストラクタ"""
        self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "recorder"))
        self.config = ConfigManager.ConfigManager()

        self.filetypes = [
            "mp3",
            "wav",
        ]

    def setFileType(self, index):
        """録音音質を決定"""
        self.ftp = self.filetypes[index]
        self.log.info(f"File type determined:{self.ftp}")

    def record(self, streamUrl, path):
        """ffmpegを用いて録音"""
        self.path = path
        print(f"recordingPath is {path}")
        self.log.debug("recording...")
        ffmpeg_setting = f"{constants.FFMPEG_PATH} -i {streamUrl} -f {self.ftp} -ac 2 -vn {path}.{self.ftp}"
        self.code = subprocess.Popen(ffmpeg_setting, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return self.code

    def stop_record(self):
        """録音を終了"""
        self.log.debug("recording stoped!")
        self.code.stdin.close()
        self.code.terminate()
        notification.notify(title='録音完了', message=f'ファイルは正しく{self.path}として保存されました。', app_name='rpb', app_icon='', timeout=10, ticker='', toast=False)
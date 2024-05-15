#rpb radio recording module

import sys
import os
import subprocess
import constants
from views import token
from logging import getLogger
import ConfigManager

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
        """選択されたメニュー項目から録音音質を決定"""
        self.ftp = self.filetypes[index]
        self.log.info(f"File type determined:{self.ftp}")

    def record(self, streamUrl, fName):
        self.log.info(f"Recording: File name {'fName'}")
        #ffmpegで録音
        c = f"{constants.FFMPEG_PATH} -i {streamUrl} -f {self.ftp} -ac 2 -vn {fName}.{self.ftp}"
        code = subprocess.Popen(c.split())

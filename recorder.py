#rpb radio recording module

import sys
import os
import subprocess
import constants
from views import token
from logging import getLogger

class Recorder:
    def __init__(self):
        """コンストラクタ"""
        self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "recorder"))

    def record(self, streamUrl, fName):
        #ffmpegで録音
        c = f"{constants.FFMPEG_PATH} -i {streamUrl} -f mp3 -ac 2 -vn {fName}.mp3"
        code = subprocess.Popen(c.split())
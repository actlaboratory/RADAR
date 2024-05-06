#rpb radio recording module

import sys
import os
import subprocess
from views import token

class Recorder:
    def __init__(self):
        """コンストラクタ"""

    def record(self, streamUrl, fName):
        #ffmpegで録音
        c = f"ffmpeg -i {streamUrl} -t 15 -f mp3 -ac 2 -vn {fName}.mp3"
        code = subprocess.Popen(c.split())
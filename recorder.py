#radio recording module

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
import os

class Recorder:
    rec_status = False

    def __init__(self):
        self.log = getLogger("%s.%s" % (constants.LOG_PREFIX, "recorder"))
        self.config = ConfigManager.ConfigManager()
        #outputフォルダの存在をチェックして、なかったら作る
        BASE_RECORDING_DIR = "OUTPUT"
        self.BASE_RECORDING_DIR = BASE_RECORDING_DIR
        if not os.path.exists(BASE_RECORDING_DIR):
            os.makedirs(BASE_RECORDING_DIR)
            self.log.info("created baseRecordingDirectory")

        self.filetypes = [
            "mp3",
            "wav",
        ]

    def setFileType(self, index):
        """録音音質を決定"""
        self.ftp = self.filetypes[index]
        self.log.info(f"File type determined:{self.ftp}")

    def getFileType(self, index):
        """インデックスからファイルタイプを取得"""
        ftp = self.filetypes[index]
        print(ftp)
        return ftp

    def record(self, streamUrl, path):
        """ffmpegを用いて録音"""
        self.path = path
        self.log.debug(f"streamUrl:{streamUrl} output:{self.path}")
        print(f"{path}.{self.ftp}")
        self.rec_status = True
        ffmpeg_setting = f"{constants.FFMPEG_PATH} -i {streamUrl} -f {self.ftp} -ac 2 -vn {path}.{self.ftp}"
        self.code = subprocess.Popen(ffmpeg_setting, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return self.code

    def stop_record(self):
        """録音を終了"""
        self.log.debug("recording stoped!")
        self.code.stdin.close()
        self.code.terminate()
        notification.notify(title='録音完了', message=f'ファイルは正しく{self.path}として保存されました。', app_name='rpb', app_icon='', timeout=10, ticker='', toast=False)
        self.rec_status = False

    #ディレクトリ関連
    def create_recordingDir(self, stationid):
        """
        放送局名のディレクトリを作成
        これは録音ファイルがディレクトリ内で散らばらないようにするための対策
        """
        dir = f"{self.BASE_RECORDING_DIR}\{stationid}"
        if not os.path.exists(dir):
            os.makedirs(dir)
        return dir


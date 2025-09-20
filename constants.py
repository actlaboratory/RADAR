# -*- coding: utf-8 -*-
#constant values
#Copyright (C) 20XX anonimous <anonimous@sample.com>

import wx
import os.path

#アプリケーション基本情報
APP_FULL_NAME = "Radio Audio Data Archive and Recorder "#アプリケーションの完全な名前
APP_NAME="RADAR"#アプリケーションの名前
APP_ICON = "radar.ico"
APP_VERSION="0.0.1"
APP_LAST_RELEASE_DATE="9999-99-99"
APP_COPYRIGHT_YEAR="20xx"
APP_LICENSE="Apache License 2.0"
APP_DEVELOPERS="actlaboratory"
APP_DEVELOPERS_URL="https://actlab.org/"
APP_DETAILS_URL="https://actlab.org/software/"
APP_COPYRIGHT_MESSAGE = "Copyright (c) %s %s All lights reserved." % (APP_COPYRIGHT_YEAR, APP_DEVELOPERS)
SUPPORTING_LANGUAGE={"ja-JP": "日本語","en-US": "English"}
FFMPEG_PATH = os.path.abspath("bin\\ffmpeg.exe")
#各種ファイル名
LOG_PREFIX="app"
LOG_FILE_NAME="RADAR.log"
SETTING_FILE_NAME="settings.ini"
KEYMAP_FILE_NAME="keymap.ini"
FFMPEG_LOG_FILE = "ffmpeg_log.txt"
PROGRAM_CACHE_DB_NAME = "program_cache.db"



#フォントの設定可能サイズ範囲
FONT_MIN_SIZE=5
FONT_MAX_SIZE=35

#３ステートチェックボックスの状態定数
NOT_CHECKED=wx.CHK_UNCHECKED
HALF_CHECKED=wx.CHK_UNDETERMINED
FULL_CHECKED=wx.CHK_CHECKED

#build関連定数
BASE_PACKAGE_URL = None
PACKAGE_CONTAIN_ITEMS = ("bin","radar.ico")#パッケージに含めたいファイルやfolderがあれば指定
NEED_HOOKS = ()#pyinstallerのhookを追加したい場合は指定
STARTUP_FILE = "RADAR.py"#起動用ファイルを指定
UPDATER_URL = "https://github.com/actlabo   atory/updater/releases/download/1.0.0/updater.zip"

# update情報
UPDATE_URL = "https://actlab.org/api/checkUpdate"
UPDATER_VERSION = "1.0.0"
UPDATER_WAKE_WORD = "hello"


#メニュー項目の定数
RECORDING_MP3 = 10000
RECORDING_WAV = 10001
RECORDING_MANAGE = 10002
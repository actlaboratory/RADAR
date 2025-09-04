# -*- coding: utf-8 -*-
#Application startup file

import os
import sys
import simpleDialog
import requests.exceptions
import traceback
import winsound
from soundPlayer import player
from soundPlayer.constants import *


#64bitのPythonでは起動させない
if sys.maxsize > (2 ** 32):
	print("64 bit環境では起動できません。")
	exit()

#カレントディレクトリを設定
if hasattr(sys,"frozen"): os.chdir(os.path.dirname(sys.executable))
else: os.chdir(os.path.abspath(os.path.dirname(__file__)))

def exchandler(type, exc, tb):
	msg=traceback.format_exception(type, exc, tb)
	print(msg)
	if type == requests.exceptions.ConnectionError:
		simpleDialog.errorDialog(_("通信に失敗しました。インターネット接続を確認してください。プログラムを終了します。"))
		os._exit(1)
		return
	elif type == requests.exceptions.ProxyError:
		simpleDialog.errorDialog(_("通信に失敗しました。プロキシサーバーの設定を確認してください。プログラムを終了します。"))
		os._exit(1)
		return
	if not hasattr(sys, "frozen"):
		print("".join(msg))
		winsound.Beep(1000, 1100)
		try:
			globalVars.app.say(str(msg[-1]))
		except:
			pass
	else:
		simpleDialog.winDialog("error", "An error has occurred. Contact to the developer for further assistance. Detail:" + "\n".join(msg[-2:]))
	try:
		f=open("errorLog.txt", "a")
		f.writelines(msg)
		f.close()
	except:
		pass
	os._exit(1	)
	player.player().exit()


sys.excepthook=exchandler

#Python3.8対応
#dllやモジュールをカレントディレクトリから読み込むように設定
if sys.version_info.major>=3 and sys.version_info.minor>=8:
	os.add_dll_directory(os.path.dirname(os.path.abspath(__file__)))
	sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import app as application
import globalVars

def main():
	try:
		if os.path.exists("errorLog.txt"):
			os.remove("errorLog.txt")
	except:
		pass
	app=application.Main()
	globalVars.app=app
	app.initialize()
	app.MainLoop()
	app.config.write()

#global schope
if __name__ == "__main__": main()

# -*- coding: utf-8 -*-
# main view
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
import tcutil
import time
import locale
import os


import constants
import globalVars
import update
import menuItemsStore
import urllib
from .base import *
from simpleDialog import *
from views import globalKeyConfig
from views import sample
from views import settingsDialog
from views import versionDialog
from views import programmanager
from views import radioManager
from views import recordingHandler
from views import programInfoHandler
from views import volumeHandler
from views import programCacheController
from views import programSearchDialog


class MainView(BaseView):
	def __init__(self):
		super().__init__("mainView")
		self.log.debug("created")
		self.events = Events(self, self.identifier)
		title = constants.APP_NAME
		super().Initialize(
			title,
			self.app.config.getint(self.identifier, "sizeX", 800, 400),
			self.app.config.getint(self.identifier, "sizeY", 600, 300),
			self.app.config.getint(self.identifier, "positionX", 50, 0),
			self.app.config.getint(self.identifier, "positionY", 50, 0)
		)
		self.InstallMenuEvent(Menu(self.identifier), self.events.OnMenuSelect)

		# outputディレクトリの存在チェックと作成
		self._ensure_output_directory()

		# プログラム管理の初期化
		self.progs = programmanager.ProgramManager()
		
		# 各ハンドラーの初期化
		self.radio_manager = radioManager.RadioManager(self)
		self.recording_handler = recordingHandler.RecordingHandler(self)
		self.program_info_handler = programInfoHandler.ProgramInfoHandler(self)
		self.volume_handler = volumeHandler.VolumeHandler(self)
		
		# エリア判定（放送局リストの初期化）
		self.radio_manager.areaDetermination(self.progs)
		
		# 番組キャッシュコントローラーの初期化（放送局リスト初期化後）
		self.program_cache_controller = programCacheController.ProgramCacheController(self.radio_manager)
		
		# UIの設定
		self.radio_manager.setup_radio_ui()
		self.program_info_handler.setup_program_info_ui()
		self.exit_button()

	def _ensure_output_directory(self):
		"""outputディレクトリの存在をチェックし、存在しない場合は作成する"""
		output_dir = "output"
		try:
			if not os.path.exists(output_dir):
				os.makedirs(output_dir)
				self.log.info(f"Created output directory: {output_dir}")
			else:
				self.log.debug(f"Output directory already exists: {output_dir}")
		except Exception as e:
			self.log.error(f"Failed to create output directory: {e}")
			errorDialog(_("outputディレクトリの作成に失敗しました。\nアプリケーションを続行しますが、録音機能が正常に動作しない可能性があります。"))

	def exit_button(self):
		self.exitbtn = self.creator.button(_("終了"), self.events.onExit)

	def get_latest_info(self):
		"""ctrl+f5によるリロード処理のときに呼ばれる"""
		self.program_info_handler.get_latest_info()

	def get_latest_programList(self):
		"""f5押したら呼ばれる"""
		self.radio_manager.get_latest_programList(self.progs)

class Menu(BaseMenu):
	def Apply(self, target):
		"""指定されたウィンドウに、メニューを適用する。"""

		# メニュー内容をいったんクリア
		self.hMenuBar = wx.MenuBar()

		# メニューの大項目を作る
		self.hFileMenu = wx.Menu()
		self.hFunctionMenu = wx.Menu()
		self.hRecordingMenu = wx.Menu()
		self.hRecordingFileTypeMenu = wx.Menu()
		self.hRecordingFileTypeMenu.Bind(wx.EVT_MENU, self.parent.events.onRecordMenuSelect)
		self.hProgramListMenu = wx.Menu()
		self.hOptionMenu = wx.Menu()
		self.hHelpMenu = wx.Menu()

		# ファイルメニュー
		self.RegisterMenuCommand(self.hFileMenu, {
			"FILE_EXAMPLE": self.parent.events.example,
			"FILE_RELOAD": self.parent.events.onReLoad,
			"EXIT":self.parent.events.onExit,
		})

		# 機能メニュー
		self.RegisterMenuCommand(self.hFunctionMenu, {
			"FUNCTION_PLAY_PLAY": self.parent.events.onRadioActivated,
			"FUNCTION_VOLUME_UP": self.parent.events.volume_up,
			"FUNCTION_VOLUME_DOWN": self.parent.events.volume_down,
			"FUNCTION_PLAY_MUTE": self.parent.events.onMute,
			"FUNCTION_OUTPUT_CHANGEDEVICE": self.parent.events.changeOutputDevice,
		})

		# 番組メニュー
		self.RegisterMenuCommand(self.hProgramListMenu, {
			"SHOW_PROGRAMLIST": self.parent.events.initializeInfoView,
			"HIDE_PROGRAMINFO": self.parent.events.switching_programInfo,
			"UPDATE_PROGRAMLIST": self.parent.events.onUpdateProgram,
			"PROGRAM_SEARCH": self.parent.events.onProgramSearch,
		})

		# 録音メニュー
		self.RegisterMenuCommand(self.hRecordingMenu, {
			"RECORDING_IMMEDIATELY": self.parent.events.record_immediately,
			"RECORDING_SCHEDULE": self.parent.events.recording_schedule,
			"RECORDING_SCHEDULE_MANAGE": self.parent.events.manage_schedules,
			"RECORDING_MANAGE": self.parent.events.manage_recordings,
		})

		# 録音品質選択メニュー
		self.RegisterMenuCommand(self.hRecordingMenu, "RECORDING_OPTION", subMenu=self.hRecordingFileTypeMenu)
		# 録音品質選択メニューの中身
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_MP3, "mp3")
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_WAV, "wav")

		# オプションメニュー
		self.RegisterMenuCommand(self.hOptionMenu, {
			"OPTION_OPTION": self.parent.events.option,
			"OPTION_KEY_CONFIG": self.parent.events.keyConfig,
		})

		# ヘルプメニュー
		self.RegisterMenuCommand(self.hHelpMenu, {
			"HELP_UPDATE": self.parent.events.checkUpdate,
			"HELP_VERSIONINFO": self.parent.events.version,
		})

		# メニューバーの生成
		self.hMenuBar.Append(self.hFileMenu, _("ファイル(&F))"))
		self.hMenuBar.Append(self.hFunctionMenu, _("機能(&F)"))
		self.hMenuBar.Append(self.hProgramListMenu, _("番組(&p)"))
		self.hMenuBar.Append(self.hRecordingMenu, _("録音(&r)"))
		self.hMenuBar.Append(self.hOptionMenu, _("オプション(&O)"))
		self.hMenuBar.Append(self.hHelpMenu, _("ヘルプ(&H)"))
		target.SetMenuBar(self.hMenuBar)


class Events(BaseEvents):
	playing = False
	mute_status = False
	displaying = True  # 番組情報表示中
	id = None
	selected = None


	def onUpdateProcess(self, event):
		"""番組情報を定期的に更新"""
		if self.playing and self.id:
			# 現在再生中の放送局の番組情報を更新
			if hasattr(self.parent, 'program_info_handler'):
				self.parent.program_info_handler.get_latest_info()

	def example(self, event):
		d = sample.Dialog()
		d.Initialize()
		r = d.Show()

	def onExit(self, event):
		if globalVars.app.config.getboolean("general", "minimizeOnExit", True):
			self.hide()
			return
		else:
			# 最小化設定が無効な場合、または他の終了方法の場合は通常通り終了
			self.exit()
			return

	def hide(self):
		self.parent.hFrame.Hide()
		return

	def show(self):
		self.parent.hFrame.Show()
		self.parent.hPanel.SetFocus()
		return

	def exit(self):
		# 録音中かどうかを確認
		from recorder import recorder_manager
		active_recorders = recorder_manager.get_active_recorders()
		
		if active_recorders:
			# 録音中の場合は確認ダイアログを表示
			recording_count = len(active_recorders)
			message = f"現在{recording_count}件の録音が進行中です。\nアプリケーションを終了しますか？\n\n録音を続行する場合は「キャンセル」を選択してください。"
			
			result = yesNoDialog(_("録音中の終了確認"), message)
			if not result:
				# キャンセルが選択された場合は終了しない
				return
		
		try:
			# 各ハンドラーのクリーンアップ
			if hasattr(self.parent, 'recording_handler'):
				self.parent.recording_handler.cleanup()
			if hasattr(self.parent, 'radio_manager'):
				self.parent.radio_manager.exit()
			self.log.info("Application cleanup completed")
		except Exception as e:
			self.log.error(f"Error during application cleanup: {e}")
		globalVars.app.tb.Destroy()
		self.parent.hFrame.Close(True)


	def option(self, event):
		d = settingsDialog.Dialog()
		d.Initialize()
		d.Show()

	def keyConfig(self, event):
		if self.setKeymap(self.parent.identifier, _("ショートカットキーの設定"), filter=keymap.KeyFilter().SetDefault(False, False)):
			# ショートカットキーの変更適用とメニューバーの再描画
			self.parent.menu.InitShortcut()
			self.parent.menu.ApplyShortcut(self.parent.hFrame)
			self.parent.menu.Apply(self.parent.hFrame)

	def checkUpdate(self, event):
		update.checkUpdate()

	def version(self, event):
		d = versionDialog.dialog()
		d.Initialize()
		r = d.Show()

	def setKeymap(self, identifier, ttl, keymap=None, filter=None):
		if keymap:
			try:
				keys = keymap.map[identifier.upper()]
			except KeyError:
				keys = {}
		else:
			try:
				keys = self.parent.menu.keymap.map[identifier.upper()]
			except KeyError:
				keys = {}
		keyData = {}
		menuData = {}
		for refName in defaultKeymap.defaultKeymap[identifier].keys():
			title = menuItemsDic.getValueString(refName)
			if refName in keys:
				keyData[title] = keys[refName]
			else:
				keyData[title] = _("なし")
			menuData[title] = refName

		d = globalKeyConfig.Dialog(keyData, menuData, [], filter)
		d.Initialize(ttl)
		if d.Show() == wx.ID_CANCEL:
			return False

		keyData, menuData = d.GetValue()

		# キーマップの既存設定を置き換える
		newMap = ConfigManager.ConfigManager()
		newMap.read(constants.KEYMAP_FILE_NAME)
		for name, key in keyData.items():
			if key != _("なし"):
				newMap[identifier.upper()][menuData[name]] = key
			else:
				newMap[identifier.upper()][menuData[name]] = ""
		newMap.write()
		return True

	def onRadioActivated(self, event):
		if not hasattr(self.parent, 'radio_manager'):
			return
		self.id = self.parent.radio_manager.tree.GetItemData(self.parent.radio_manager.tree.GetFocusedItem())  # stationIDが出る
		if self.id == None:
			return
		self.parent.log.info("activated" + self.id)
		try:
			if not self.playing:
				self.parent.radio_manager.play(self.id, self.parent.progs)
			else:
				self.parent.radio_manager.stop()
		except urllib.request.HTTPError as error:
			errorDialog(_("再生に失敗しました。聴取可能な都道府県内であることをご確認ください。\nこの症状が引き続き発生する場合は、放送局一覧を再描画してからお試しください。"))
			self.parent.log.error("Playback failure!" + str(error))
			return

		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.nplist.Enable()
			self.parent.program_info_handler.nplist.clear()
			self.show_program_info()
			self.show_onair_music()

			# メニュー項目の表示
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"), True)

			# 番組の説明
			self.show_description()

	def show_description(self):
		"""番組の説明を表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.show_description(self.id)

	def show_program_info(self):
		"""番組情報を表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.show_program_info(self.id)

	def show_onair_music(self):
		"""オンエア曲情報を表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.show_onair_music(self.id)

	def onRadioSelected(self, event):
		if hasattr(self.parent, 'radio_manager'):
			self.selected = self.parent.radio_manager.tree.GetItemData(self.parent.radio_manager.tree.GetFocusedItem())
		if self.selected == None:
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_PROGRAMLIST"), False)
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"), False)
			return
		
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_PROGRAMLIST"), True)
		
		# 選択した放送局の録音状態をチェックしてメニューを更新
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler._update_recording_menu_for_station(self.selected)

	def initializeInfoView(self, event):
		"""番組一覧表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.initializeInfoView(self.selected)

	def onReLoad(self, event):
		"""リロードを処理する"""
		self.parent.get_latest_info()

	def onUpdateProgram(self, event):
		"""最新の番組一覧に更新"""
		if hasattr(self.parent, 'radio_manager'):
			self.parent.radio_manager.stop()
		self.parent.get_latest_programList()

	def onRecordMenuSelect(self, event):
		"""録音品質メニューの動作"""
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler.onRecordMenuSelect(event)

	def record_immediately(self, event):
		"""録音の開始/停止を処理するメソッド"""
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler.record_immediately(event)

	def recording_schedule(self, event):
		"""録音予約ウィザードを表示"""
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler.recording_schedule(event)

	def manage_schedules(self, event):
		"""予約録音管理ダイアログを表示"""
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler.manage_schedules(event)

	def manage_recordings(self, event):
		"""録音管理ダイアログを表示"""
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler.manage_recordings(event)

	def volume_up(self, event):
		"""音量を上げる"""
		if hasattr(self.parent, 'volume_handler'):
			self.parent.volume_handler.volume_up(event)

	def volume_down(self, event):
		"""音量を下げる"""
		if hasattr(self.parent, 'volume_handler'):
			self.parent.volume_handler.volume_down(event)

	def onMute(self, event):
		"""ミュートの切り替え"""
		if hasattr(self.parent, 'volume_handler'):
			self.parent.volume_handler.onMute(event)

	def changeOutputDevice(self, event):
		"""出力デバイスを変更"""
		if hasattr(self.parent, 'volume_handler'):
			self.parent.volume_handler.changeOutputDevice(event)

	def switching_programInfo(self, event):
		"""番組情報の表示/非表示を切り替え"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.switching_programInfo(event)

	def check_recording_status(self, event):
		"""録音状態をチェックしてUIを更新"""
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler.check_recording_status(event)

	def onVolumeChanged(self, event):
		"""音量変更時の処理"""
		if hasattr(self.parent, 'volume_handler'):
			self.parent.volume_handler.onVolumeChanged(event)
	
	def onProgramSearch(self, event):
		"""番組検索ダイアログを表示"""
		# globalVars.appを使用して完全に独立した設計
		search_dialog = programSearchDialog.ProgramSearchDialog()
		search_dialog.Initialize()
		search_dialog.Show()
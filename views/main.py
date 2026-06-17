# -*- coding: utf-8 -*-
# main view
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
# Copyright (C) 2019-2021 yamahubuki <itiro.ishino@gmail.com>

import wx
import os
import win32com.client

import constants
import globalVars
import update
import menuItemsStore
import urllib
import ConfigManager
from recorder import recorder_manager
from recorder import schedule_manager
from .base import *
from simpleDialog import *
from views import globalKeyConfig
from views import settingsDialog
from views import versionDialog
from views import programmanager
from views import radioManager
from views import recordingHandler
from views import programInfoHandler
from views import volumeHandler
from views import programCacheController
from views import programSearchDialog
import shutdown_log


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

		self.events.displaying = self.app.config.getboolean(self.identifier, "displayProgramInfo", True)
		self._ensure_output_directory()
		self.progs = programmanager.ProgramManager()

		self.radio_manager = radioManager.RadioManager(self)
		self.recording_handler = recordingHandler.RecordingHandler(self)
		self.program_info_handler = programInfoHandler.ProgramInfoHandler(self)
		self.volume_handler = volumeHandler.VolumeHandler(self)

		self.radio_manager.areaDetermination(self.progs)

		self.program_cache_controller = programCacheController.ProgramCacheController(self.radio_manager)

		self.radio_manager.setup_radio_ui()
		if self.events.displaying:
			self.program_info_handler.setup_program_info_ui()
		else:
			if hasattr(self, 'menu'):
				self.menu.SetMenuLabel("HIDE_PROGRAMINFO", _("番組情報を表示(&P)"))

	def _ensure_output_directory(self):
		"""outputディレクトリの存在をチェックし、存在しない場合は作成する"""
		output_dir = "output"
		if not os.path.exists(output_dir):
			if not self._create_directory_safely(output_dir):
				self.log.error("Failed to create output directory")
				errorDialog(_("outputディレクトリの作成に失敗しました。\nアプリケーションを続行しますが、録音機能が正常に動作しない可能性があります。"))
			else:
				self.log.info(f"Created output directory: {output_dir}")
		else:
			self.log.debug(f"Output directory already exists: {output_dir}")

	def _create_directory_safely(self, directory_path):
		"""ディレクトリを安全に作成"""
		try:
			os.makedirs(directory_path)
			return True
		except (OSError, PermissionError) as e:
			self.log.error(f"Failed to create directory {directory_path}: {e}")
			return False

	def get_latest_info(self):
		"""ctrl+f5によるリロード処理のときに呼ばれる"""
		self.program_info_handler.get_latest_info()

	def get_latest_programList(self):
		"""f5押したら呼ばれる"""
		self.radio_manager.get_latest_programList(self.progs)

class Menu(BaseMenu):
	def Apply(self, target):
		"""指定されたウィンドウに、メニューを適用する。"""

		self.hMenuBar = wx.MenuBar()
		self.hFileMenu = wx.Menu()
		self.hFunctionMenu = wx.Menu()
		self.hRecordingMenu = wx.Menu()
		self.hRecordingFileTypeMenu = wx.Menu()
		self.hRecordingFileTypeMenu.Bind(wx.EVT_MENU, self.parent.events.onRecordMenuSelect)
		self.hProgramListMenu = wx.Menu()
		self.hOptionMenu = wx.Menu()
		self.hHelpMenu = wx.Menu()

		self.RegisterMenuCommand(self.hFileMenu, {
			"FILE_RELOAD": self.parent.events.onReLoad,
			"HIDE": self.parent.events.onHide,
			"EXIT":self.parent.events.onExitMenu,
		})
		
		self.hFileMenu.Bind(wx.EVT_MENU_OPEN, self.OnMenuOpen)
		self.RegisterMenuCommand(self.hFunctionMenu, {
			"FUNCTION_PLAY_PLAY": self.parent.events.onRadioActivated,
			"FUNCTION_TIMEFREE_TOGGLE": self.parent.events.onTimefreeToggle,
			"FUNCTION_VOLUME_UP": self.parent.events.volume_up,
			"FUNCTION_VOLUME_DOWN": self.parent.events.volume_down,
			"FUNCTION_PLAY_MUTE": self.parent.events.onMute,
			"FUNCTION_OUTPUT_CHANGEDEVICE": self.parent.events.changeOutputDevice,
		})

		self.RegisterMenuCommand(self.hProgramListMenu, {
			"SHOW_PROGRAMLIST": self.parent.events.initializeInfoView,
			"HIDE_PROGRAMINFO": self.parent.events.switching_programInfo,
			"UPDATE_PROGRAMLIST": self.parent.events.onUpdateProgram,
			"PROGRAM_SEARCH": self.parent.events.onProgramSearch,
		})

		self.RegisterMenuCommand(self.hRecordingMenu, {
			"RECORDING_IMMEDIATELY": self.parent.events.record_immediately,
			"RECORDING_MANAGE": self.parent.events.manage_recordings,
		})

		self.RegisterMenuCommand(self.hRecordingMenu, "RECORDING_OPTION", subMenu=self.hRecordingFileTypeMenu)
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_MP3, "mp3")
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_WAV, "wav")
		self.hRecordingFileTypeMenu.AppendCheckItem(constants.RECORDING_M4A, "m4a")

		self.RegisterMenuCommand(self.hOptionMenu, {
			"OPTION_OPTION": self.parent.events.option,
			"OPTION_KEY_CONFIG": self.parent.events.keyConfig,
			"OPTION_STARTUP": self.parent.events.registerStartup,
		})

		self.RegisterMenuCommand(self.hHelpMenu, {
			"HELP_UPDATE": self.parent.events.checkUpdate,
			"HELP_VERSIONINFO": self.parent.events.version,
		})

		self.hMenuBar.Append(self.hFileMenu, _("ファイル(&F))"))
		self.hMenuBar.Append(self.hFunctionMenu, _("再生(&P)"))
		self.hMenuBar.Append(self.hProgramListMenu, _("番組(&A)"))
		self.hMenuBar.Append(self.hRecordingMenu, _("録音(&r)"))
		self.hMenuBar.Append(self.hOptionMenu, _("オプション(&O)"))
		self.hMenuBar.Append(self.hHelpMenu, _("ヘルプ(&H)"))
		target.SetMenuBar(self.hMenuBar)
	
	def OnMenuOpen(self, event):
		"""メニューが開かれたときに設定に応じてHIDEメニューを無効化"""
		if globalVars.app.config.getboolean("general", "minimizeOnExit", True):
			self.EnableMenu("HIDE", False)
		else:
			self.EnableMenu("HIDE", True)


class Events(BaseEvents):
	playing = False
	mute_status = False
	displaying = True
	current_playing_station_id = None
	current_selected_station_id = None
	_exit_in_progress = False


	def onUpdateProcess(self, event):
		"""番組情報を定期的に更新"""
		if self.playing and self.current_playing_station_id:
			if hasattr(self.parent, 'program_info_handler'):
				self.parent.program_info_handler.get_latest_info()

	def onHide(self, event):
		"""最小化メニューが選択されたときの処理"""
		self.hide()

	def hide(self):
		self.parent.hFrame.Hide()
		self.log.info("Minimized to taskbar.")
		return

	def show(self):
		self.parent.hFrame.Show()
		self.parent.hPanel.SetFocus()
		self.log.info("Window restored.")
		return
	
	def OnExit(self, event):
		"""Alt+F4などでウィンドウを閉じようとしたときの処理"""
		if self._exit_in_progress:
			event.Skip()
			return
		if event.CanVeto():
			if globalVars.app.config.getboolean("general", "minimizeOnExit", True):
				event.Veto()
				self.hide()
				return
		self.exit(event, from_close_event=True)
		return

	def onExitMenu(self, event):
		"""ファイルメニューから「終了」が選択されたときの処理"""

		self.exit(event, from_close_event=False)

	def exit(self, event=None, from_close_event=False):
		sd = shutdown_log.get_shutdown_logger()
		if self._exit_in_progress:
			sd.warning("Shutdown already in progress; ignoring duplicate exit request")
			return
		self._exit_in_progress = True
		self.log.info("Application shutdown sequence started")
		sd.info("[Shutdown] Phase 1: Confirm exit (recording in progress / pending schedules)")
		active_recorders = recorder_manager.get_active_recorders()
		
		if active_recorders:
			recording_count = len(active_recorders)
			message = f"現在{recording_count}件の録音が進行中です。\nアプリケーションを終了しますか？\n\n録音を続行する場合は「いいえ」を選択してください。"
			
			result = yesNoDialog(_("録音中の終了確認"), message)
			if result == wx.ID_NO:
				self._exit_in_progress = False
				sd.info("[Shutdown] User cancelled exit (recording in progress)")
				if from_close_event and event and event.CanVeto():
					event.Veto()
				return
		
		pending_schedule_count = schedule_manager.count_pending_schedules_for_exit_warning()
		if pending_schedule_count > 0:
			message = f"録音予約が{pending_schedule_count}件登録されています。\nアプリケーションを終了すると、すべての予約データが削除されます。\n\n終了しますか？"
			
			result = yesNoDialog(_("予約データ削除の確認"), message)
			if result == wx.ID_NO:
				self._exit_in_progress = False
				sd.info("[Shutdown] User cancelled exit (pending schedules)")
				if from_close_event and event and event.CanVeto():
					event.Veto()
				return
		
		sd.info("[Shutdown] Phase 2: Cleanup (recording handler, radio/mpv, schedule data)")
		self._cleanup_recording_handler()
		self._cleanup_radio_manager()
		self._cleanup_program_cache()
		self._cleanup_schedule_data()
		sd.info(
			"[Shutdown] Phase 2 complete (program cache sqlite closed; MainLoop ends next)"
		)
		self.log.info("Tearing down UI resources")
		globalVars.app.tb.Destroy()
		shutdown_log.flush_app_log_handlers()

		sd.info("[Shutdown] Phase 3: Close main window (MainLoop will end)")

		if from_close_event:
			if event:
				event.Skip()
		else:
			self.parent.hFrame.Destroy()
		shutdown_log.flush_app_log_handlers()

	def _cleanup_recording_handler(self):
		"""録音ハンドラーのクリーンアップ"""
		if hasattr(self.parent, 'recording_handler'):
			try:
				shutdown_log.get_shutdown_logger().info("[Shutdown] Running recording_handler.cleanup")
				self.parent.recording_handler.cleanup()
			except Exception as e:
				self.log.error("recording_handler cleanup failed: %s", e, exc_info=True)

	def _cleanup_radio_manager(self):
		"""ラジオマネージャーのクリーンアップ"""
		if hasattr(self.parent, 'radio_manager'):
			try:
				shutdown_log.get_shutdown_logger().info("[Shutdown] Running radio_manager.exit (mpv)")
				self.parent.radio_manager.exit()
			except Exception as e:
				self.log.error("radio_manager cleanup failed: %s", e, exc_info=True)

	def _cleanup_program_cache(self):
		"""番組キャッシュのクリーンアップ"""
		if hasattr(self.parent, 'program_cache_controller'):
			try:
				shutdown_log.get_shutdown_logger().info(
					"[Shutdown] Running program_cache_controller.cleanup (sqlite)"
				)
				self.parent.program_cache_controller.cleanup()
			except Exception as e:
				self.log.error("program_cache_controller cleanup failed: %s", e, exc_info=True)

	def _cleanup_schedule_data(self):
		"""スケジュール録音データの完全削除"""
		try:
			shutdown_log.get_shutdown_logger().info(
				"[Shutdown] Removing schedule file and in-memory schedules"
			)
			schedule_file = schedule_manager.schedule_file
			if os.path.exists(schedule_file):
				os.remove(schedule_file)
				self.log.info("Deleted schedule file: %s", schedule_file)

			schedule_manager.cleanup()

			with schedule_manager.lock:
				removed_count = len(schedule_manager.schedules)
				schedule_manager.schedules.clear()
				self.log.info("Cleared %s schedule entries from memory", removed_count)

			self.log.info("Schedule data cleanup completed")

		except Exception as e:
			self.log.error("Schedule data cleanup failed: %s", e, exc_info=True)


	def option(self, event):
		d = settingsDialog.Dialog()
		d.Initialize()
		d.Show()

	def keyConfig(self, event):
		if self.setKeymap(self.parent.identifier, _("ショートカットキーの設定"), filter=keymap.KeyFilter().SetDefault(False, False)):
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
		if self.parent.radio_manager.is_timefree_playing():
			result = yesNoDialog(
				_("聴き逃し再生中"),
				_("現在、聴き逃し配信を再生中です。\nライブ再生へ切り替えますか？")
			)
			if result != wx.ID_YES:
				return
			try:
				self.parent.radio_manager.stop_timefree()
			except Exception as e:
				self.log.error(f"Failed to stop timefree before live playback: {e}")
		
		self.current_playing_station_id = self.parent.radio_manager.tree.GetItemData(
			self.parent.radio_manager.tree.GetFocusedItem()
		)
		if self.current_playing_station_id is None:
			return
		
		self.parent.log.info("activated" + self.current_playing_station_id)
		self._handle_playback_toggle()
		self._update_program_info_display()

	def _handle_playback_toggle(self):
		"""再生/停止の切り替え処理"""
		if not self.playing:
			if not self._start_playback():
				return
		else:
			self._stop_playback()

	def _start_playback(self):
		"""再生開始処理"""
		try:
			self.parent.radio_manager.play(self.current_playing_station_id, self.parent.progs)
			return True
		except urllib.request.HTTPError as error:
			errorDialog(_("再生に失敗しました。聴取可能な都道府県内であることをご確認ください。\nこの症状が引き続き発生する場合は、放送局一覧を再描画してからお試しください。"))
			self.parent.log.error("Playback failure!" + str(error))
			return False

	def _stop_playback(self):
		"""再生停止処理"""
		self.parent.radio_manager.stop()

	def onTimefreeToggle(self, event):
		"""聴き逃し停止、または前回停止した聴き逃しを再開（無再生時は確認なし・ライブ中は確認あり）"""
		if not hasattr(self.parent, "radio_manager"):
			return

		radio_manager = self.parent.radio_manager
		if radio_manager.is_timefree_playing():
			radio_manager.stop_timefree()
			return

		if not radio_manager.should_enable_timefree_menu_command():
			return

		if not radio_manager.has_last_timefree_request():
			return

		if radio_manager.is_live_playing():
			result = yesNoDialog(
				_("確認"),
				_("ライブ再生を終了し、前回停止した聴き逃し再生を再開しますか？"),
				self.parent.hFrame,
			)
			if result != wx.ID_YES:
				return

		try:
			radio_manager.replay_last_timefree()
		except Exception as e:
			self.log.warning(f"Failed to replay last timefree stream: {e}")
			errorDialog(_("聴き逃し再生の再開に失敗しました。\n%(detail)s") % {"detail": str(e)}, self.parent.hFrame)

	def _update_program_info_display(self):
		"""番組情報表示の更新"""
		if not hasattr(self.parent, 'program_info_handler'):
			return
		
		if not self.displaying:
			return
		
		handler = self.parent.program_info_handler
		handler.ensure_program_info_ui()
		handler.nplist.Enable()
		handler.nplist.clear()
		self.show_program_info()
		self.show_onair_music()
		self.show_description()

		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"), True)

	def show_description(self):
		"""番組の説明を表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.show_description(self.current_playing_station_id)

	def show_program_info(self):
		"""番組情報を表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.show_program_info(self.current_playing_station_id)

	def show_onair_music(self):
		"""オンエア曲情報を表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.show_onair_music(self.current_playing_station_id)

	def onRadioSelected(self, event):
		if not hasattr(self.parent, 'radio_manager'):
			return
		
		self.current_selected_station_id = self.parent.radio_manager.tree.GetItemData(
			self.parent.radio_manager.tree.GetFocusedItem()
		)
		self._update_menu_for_selected_station()

	def _update_menu_for_selected_station(self):
		"""選択された放送局に応じてメニューを更新"""
		if self.current_selected_station_id is None:
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_PROGRAMLIST"), False)
			self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"), False)
			return
		
		self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("SHOW_PROGRAMLIST"), True)
		
		if hasattr(self.parent, 'recording_handler'):
			self.parent.recording_handler._update_recording_menu_for_station(self.current_selected_station_id)

	def initializeInfoView(self, event):
		"""番組一覧表示"""
		if hasattr(self.parent, 'program_info_handler'):
			self.parent.program_info_handler.initializeInfoView(self.current_selected_station_id)

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

		search_dialog = programSearchDialog.ProgramSearchDialog()
		search_dialog.Initialize()
		search_dialog.Show()

	def registerStartup(self, event):
		"""Windows起動時の自動起動を設定/解除する"""
		target = os.path.join(
			os.environ["appdata"],
			"Microsoft",
			"Windows",
			"Start Menu",
			"Programs",
			"Startup",
			"%s.lnk" % constants.APP_NAME
		)
		if os.path.exists(target):
			d = yesNoDialog(_("確認"), _("Windows起動時の自動起動はすでに設定されています。設定を解除しますか？"))
			if d == wx.ID_YES:
				os.remove(target)
				dialog(_("完了"), _("Windows起動時の自動起動を無効化しました。"))
			return
		ws = win32com.client.Dispatch("wscript.shell")
		shortCut = ws.CreateShortcut(target)
		shortCut.TargetPath = globalVars.app.getAppPath()
		shortCut.Save()
		dialog(_("完了"), _("Windows起動時の自動起動を設定しました。"))
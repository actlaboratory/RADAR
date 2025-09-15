# -*- coding: utf-8 -*-
# 録音処理ハンドラーモジュール

import wx
import time
import datetime
import re
from plyer import notification
from simpleDialog import *
from views import recordingWizzard
from views import scheduledRecordingManager
from views import recordingManager
import menuItemsStore


class RecordingHandler:
    def __init__(self, parent_view):
        self.parent = parent_view
        self.log = parent_view.log
        self.app = parent_view.app
        self.menu = parent_view.menu
        self.events = parent_view.events
        self.recordingStatusTimer = wx.Timer()
        
        # 録音設定の初期化
        self._init_recording_settings()
        
        # 録音スケジュール監視を開始
        self._start_schedule_monitoring()
        
        # 録音状態監視タイマーを開始
        self.recordingStatusTimer.Start(5000)  # 5秒ごとにチェック
        self.recordingStatusTimer.Bind(wx.EVT_TIMER, self.events.check_recording_status)

    def _init_recording_settings(self):
        """録音設定を初期化"""
        try:
            menu_id = self.app.config.getint("recording", "menu_id")
            check_menu = self.app.config.getboolean("recording", "check_menu")
        except:
            # 設定が存在しない場合はデフォルト値を使用
            menu_id = 10000  # MP3
            check_menu = True
            # 設定を保存
            self.app.config["recording"]["menu_id"] = menu_id
            self.app.config["recording"]["check_menu"] = check_menu
        
        self.parent.menu.hRecordingFileTypeMenu.Check(menu_id, check_menu)
        self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("HIDE_PROGRAMINFO"), False)
        self._update_schedule_menu_status()

    def _start_schedule_monitoring(self):
        """録音スケジュール監視を開始"""
        try:
            from recorder import schedule_manager
            schedule_manager.start_monitoring()
            self.log.info("Recording schedule monitoring started")
        except Exception as e:
            self.log.error(f"Failed to start schedule monitoring: {e}")

    def onRecordMenuSelect(self, event):
        """録音品質メニューの動作"""
        selected = event.GetId()
        
        # 排他的選択（ラジオボタン的な動作）
        if selected == 10000 and self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected):
            self.parent.menu.hRecordingFileTypeMenu.Check(10001, False)  # WAVをオフ
        elif selected == 10001 and self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected):
            self.parent.menu.hRecordingFileTypeMenu.Check(10000, False)  # MP3をオフ
        
        # 設定を保存
        self.parent.app.config["recording"]["menu_id"] = selected
        self.parent.app.config["recording"]["check_menu"] = self.parent.menu.hRecordingFileTypeMenu.IsChecked(selected)
        
        # デバッグログ
        filetype = "mp3" if selected == 10000 else "wav"
        self.log.info(f"Recording file type changed to: {filetype}")

    def record_immediately(self, event):
        """録音の開始/停止を処理するメソッド"""
        if self.events.selected is None:
            self.log.debug("No station selected for recording")
            return

        # 選択した放送局の録音状態をチェック
        from recorder import recorder_manager
        is_station_recording = self._is_station_recording(self.events.selected)
        
        # 録音中の場合は停止処理
        if is_station_recording:
            self._stop_station_recording(self.events.selected)
            return

        # 録音開始処理
        try:
            # 現在の番組情報を取得
            title = self.parent.progs.getNowProgram(self.events.selected)
            if not title:
                self.log.warning("Failed to get program title, using fallback")
                # 番組タイトルが取得できない場合は、放送局名と時刻を使用
                current_time = datetime.datetime.now().strftime("%H%M")
                title = f"番組不明_{current_time}"
            else:
                # 番組タイトルをファイル名に適した形式に変換
                title = re.sub(r'[<>:"/\\|?*]', '_', title)  # 無効な文字を置換
                title = title.strip()

            # 重複録音チェック
            if recorder_manager.is_duplicate_recording(self.events.selected, title):
                self.log.warning(f"Duplicate recording detected: {self.parent.radio_manager.stid[self.events.selected]} - {title}")
                # 既存の録音情報を取得
                existing_info = recorder_manager.get_recording_info(self.events.selected, title)
                if existing_info:
                    start_time_str = datetime.datetime.fromtimestamp(existing_info["start_time"]).strftime("%H:%M")
                    errorDialog(_(f"同じ番組の録音が既に開始されています。\n\n放送局: {self.parent.radio_manager.stid[self.events.selected]}\n番組: {title}\n開始時刻: {start_time_str}"))
                else:
                    errorDialog(_(f"同じ番組の録音が既に開始されています。\n\n放送局: {self.parent.radio_manager.stid[self.events.selected]}\n番組: {title}"))
                return

            # ストリームURLの取得
            self.parent.radio_manager.get_streamUrl(self.events.selected, self.parent.progs)
            if not self.parent.radio_manager.m3u8:
                self.log.error("Failed to get stream URL")
                errorDialog(_("ストリームURLの取得に失敗しました。"))
                return

            # ファイル名とディレクトリの準備
            replace = title.replace(" ", "-")
            station_dir = self.parent.radio_manager.stid[self.events.selected].replace(" ", "_")
            from recorder import create_recording_dir
            dirs = create_recording_dir(station_dir, title)
            
            # タイムスタンプを追加してファイル名重複を回避
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = f"{dirs}\{timestamp}_{replace}"
            
            # ファイルタイプを取得（現在のメニュー選択状態から）
            if self.parent.menu.hRecordingFileTypeMenu.IsChecked(10001):  # WAV
                filetype = "wav"
            else:  # MP3（デフォルト）
                filetype = "mp3"
            self.log.info(f"Recording with file type: {filetype}")
            
            stream_url = self.parent.radio_manager.m3u8
            end_time = time.time() + (8 * 3600)  # 8時間後
            info = f"{self.parent.radio_manager.stid[self.events.selected]} {title}"
            
            # 録音完了時のコールバック
            def on_recording_complete(recorder):
                self._update_recording_menu_for_station(self.events.selected)
                try:
                    notification.notify(
                        title='録音完了',
                        message=f'{title} の録音が完了しました。',
                        app_name='rpb',
                        timeout=10
                    )
                    self.log.info(f"Recording completion notification sent successfully: {title}")
                except Exception as e:
                    self.log.error(f"Failed to send recording completion notification: {e}")
            
            recorder = recorder_manager.start_recording(stream_url, file_path, info, end_time, filetype, on_recording_complete, self.events.selected, title)
            if recorder:
                self.log.info(f"Recording started: {title}")
                self._update_recording_menu_for_station(self.events.selected)
                # 録音開始時の通知
                try:
                    notification.notify(
                        title='録音開始',
                        message=f'{title} の録音を開始しました。',
                        app_name='rpb',
                        timeout=10
                    )
                    self.log.info(f"Recording start notification sent successfully: {title}")
                except Exception as e:
                    self.log.error(f"Failed to send recording start notification: {e}")
            else:
                self.log.error("Recording failed to start")
                errorDialog(_("録音の開始に失敗しました。"))
        
        except Exception as e:
            self.log.error(f"Error during recording start: {e}")
            errorDialog(_("録音の開始中にエラーが発生しました。"))
            self._update_recording_menu_for_station(self.events.selected)

    def check_recording_status(self, event):
        """録音状態をチェックしてUIを更新"""
        try:
            # 現在選択されている放送局がある場合、その放送局の録音状態をチェック
            if self.events.selected:
                self._update_recording_menu_for_station(self.events.selected)
            
            # 予約録音の状態をチェックしてメニュー項目を更新
            self._update_schedule_menu_status()
                    
        except Exception as e:
            self.log.error(f"Error checking recording status: {e}")

    def _update_schedule_menu_status(self):
        """予約録音の状態に応じてメニュー項目を更新"""
        try:
            # 予約録音管理メニューは常に有効
            pass
        except Exception as e:
            self.log.error(f"Error updating schedule menu status: {e}")

    def _is_station_recording(self, station_id):
        """指定された放送局が録音中かどうかを判定"""
        try:
            from recorder import recorder_manager
            return recorder_manager.is_station_recording(station_id)
        except Exception as e:
            self.log.error(f"Error checking station recording status: {e}")
            return False

    def _stop_station_recording(self, station_id):
        """指定された放送局の録音を停止"""
        try:
            from recorder import recorder_manager
            stopped_count = recorder_manager.stop_station_recording(station_id)
            
            if stopped_count > 0:
                self.log.info(f"Stopped {stopped_count} recording(s) for station: {station_id}")
                try:
                    notification.notify(
                        title='録音停止',
                        message=f'{self.parent.radio_manager.stid.get(station_id, station_id)} の録音を停止しました。',
                        app_name='rpb',
                        timeout=10
                    )
                    self.log.info(f"Recording stop notification sent successfully for station: {station_id}")
                except Exception as e:
                    self.log.error(f"Failed to send recording stop notification: {e}")
            else:
                self.log.warning(f"No active recordings found for station: {station_id}")
            
            # メニューを更新（件数表示のみ）
            self._update_recording_menu_for_station(station_id)
            
        except Exception as e:
            self.log.error(f"Error stopping station recording: {e}")
            errorDialog(_("録音の停止中にエラーが発生しました。"))

    def _update_recording_menu_for_station(self, station_id):
        """指定された放送局の録音状態に応じてメニューを更新（件数表示のみ）"""
        try:
            from recorder import recorder_manager
            active_count = len(recorder_manager.get_active_recorders())
            
            # 件数表示のみでメニューラベルを更新
            if active_count > 0:
                self.parent.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("今すぐ録音(&R)") + f" ({active_count}件録音中)")
            else:
                self.parent.menu.SetMenuLabel("RECORDING_IMMEDIATELY", _("今すぐ録音(&R)"))
            
            self.parent.menu.hMenuBar.Enable(menuItemsStore.getRef("RECORDING_IMMEDIATELY"), True)
            
        except Exception as e:
            self.log.error(f"Error updating recording menu for station: {e}")

    def recording_schedule(self, event):
        """録音予約ウィザードを表示"""
        try:
            # 常に新規作成ウィザードを表示
            rw = recordingWizzard.RecordingWizzard(self.events.selected, self.parent.radio_manager.stid[self.events.selected])
            rw.Show()
                
        except Exception as e:
            self.log.error(f"Error in recording schedule: {e}")
            errorDialog(_("録音予約の処理に失敗しました。"))

    def manage_schedules(self, event):
        """予約録音管理ダイアログを表示"""
        try:
            dialog = scheduledRecordingManager.ScheduledRecordingManager()
            dialog.Initialize()
            dialog.Show()
            
        except Exception as e:
            self.log.error(f"Error in manage_schedules: {e}")
            errorDialog(f"予約録音管理の表示に失敗しました: {e}")

    def manage_recordings(self, event):
        """録音管理ダイアログを表示"""
        try:
            dialog = recordingManager.RecordingManagerDialog()
            dialog.Initialize()
            dialog.Show()
            
        except Exception as e:
            self.log.error(f"Error in manage_recordings: {e}")
            errorDialog(f"録音管理の表示に失敗しました: {e}")

    def cleanup(self):
        """終了時のクリーンアップ"""
        try:
            # 録音状態監視タイマーを停止
            if self.recordingStatusTimer:
                self.recordingStatusTimer.Stop()
            
            # 録音スケジュールのクリーンアップ
            from recorder import schedule_manager
            schedule_manager.cleanup()
            
            # 全ての録音を停止
            from recorder import recorder_manager
            recorder_manager.cleanup()
            
            self.log.info("Recording cleanup completed")
        except Exception as e:
            self.log.error(f"Error during recording cleanup: {e}")

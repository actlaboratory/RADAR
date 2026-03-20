import wx
import time
import datetime
import locale
import re
import threading
from logging import getLogger
from notification_util import notify as notification_notify
import globalVars
import simpleDialog
from views import showRadioProgramScheduleListBase
from views import programmanager
import tcutil
from recorder import schedule_manager, RecordingSchedule
import os


class RecordingWizzard(showRadioProgramScheduleListBase.ShowSchedule):
    def __init__(self, stid, radioname):
        super().__init__(stid, radioname)
        self.config = globalVars.app.config
        self.stid = stid
        self.radioname = radioname
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
        from recorder import get_file_type_from_config
        self.filetype = get_file_type_from_config()
        self.current_schedule = None
        self._current_timefree_duration_sec = 0
        self._updating_seek_ui = False
        self.timefree_seek_timer = None
        self.timefree_seek_apply_timer = None
        self._pending_seek_seconds = None
        self._seek_worker_running = False
        self._seek_worker_lock = threading.Lock()
        self._is_disposed = False
        self._main_window = globalVars.app.hMainView.hFrame
        super().Initialize()
        self.log = getLogger("recording_wizzard")
        self._main_window.Bind(wx.EVT_CLOSE, self.on_application_close)
        self.timefree_seek_timer = wx.Timer(self.wnd)
        self.wnd.Bind(wx.EVT_TIMER, self.onTimefreeSeekTimer, self.timefree_seek_timer)
        self.timefree_seek_apply_timer = wx.Timer(self.wnd)
        self.wnd.Bind(wx.EVT_TIMER, self.onTimefreeSeekApplyTimer, self.timefree_seek_apply_timer)

    def _refresh_selected_filetype(self):
        """録音形式設定を最新化して返す"""
        from recorder import get_file_type_from_config
        self.filetype = get_file_type_from_config()
        return self.filetype

    def _is_timefree_playing(self):
        main_view = globalVars.app.hMainView
        if not hasattr(main_view, "radio_manager"):
            return False
        return main_view.radio_manager.is_timefree_playing()

    def _update_timefree_button_label(self):
        main_view = globalVars.app.hMainView
        is_timefree_playing = self._is_timefree_playing()
        if is_timefree_playing:
            self.timefree_play_btn.SetLabel(_("聴き逃し停止(&P)"))
            self.timefree_play_btn.Enable(True)
        else:
            self.timefree_play_btn.SetLabel(_("聴き逃し再生(&P)"))
            is_live_playing = False
            if hasattr(main_view, "radio_manager"):
                is_live_playing = main_view.radio_manager.is_live_playing()
            self.timefree_play_btn.Enable(not is_live_playing)
        if hasattr(self, "timefree_seek_slider"):
            self.timefree_seek_slider.Enable(is_timefree_playing)
        if hasattr(main_view, "radio_manager"):
            main_view.radio_manager.update_timefree_command_ui()

    def onDialogActivated(self, event):
        """ダイアログ再アクティブ時にボタン状態を再同期"""
        if self._is_disposed:
            event.Skip()
            return
        self._update_timefree_button_label()
        self._sync_seek_slider_from_player()
        event.Skip()

    def get_streamUrl(self, stationid):
        """ストリームURLを取得"""
        try:
            return self.progs.get_authenticated_stream_url(stationid)
        except Exception as e:
            self.log.error(f"Failed to get stream URL: {e}")

    def onFinishButton(self, event):
        """録音予約を確定"""
        try:
            self._refresh_selected_filetype()
            current = datetime.datetime.now()
            program_title, start_dt, end_dt = self._get_selected_program_range()

            # 日時オブジェクトを設定
            self.stdt = start_dt
            self.endt = end_dt

            current = datetime.datetime.now()

            # 過去の番組かチェック
            if self.stdt < current:
                simpleDialog.errorDialog("過去の番組の録音はできません。番組を選び直してください。")
                self.log.error(f"Failed to schedule program: Specified time ({self.stdt}) is in the past.")
                return
            
            # 出力パスを準備
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', program_title).strip()
            replace = safe_title.replace(" ", "-")
            # 設定から出力先フォルダを取得
            from recorder import create_recording_dir
            station_dir = self.radioname.replace(" ", "_")
            dirs = create_recording_dir(station_dir, program_title)
            
            # タイムスタンプを追加してファイル名重複を回避
            timestamp = self.stdt.strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(dirs, f"{timestamp}_{replace}")
            
            # 録音予約を作成
            schedule = RecordingSchedule(
                station_id=self.stid,
                station_name=self.radioname,
                program_title=program_title,
                start_time=self.stdt,
                end_time=self.endt,
                output_path=output_path,
                filetype=self.filetype
            )
            
            # 予約を追加
            schedule_manager.add_schedule(schedule)
            self.current_schedule = schedule
            
            # 監視を開始（初回のみ）
            schedule_manager.start_monitoring()
            
            # 現在のスケジュール数を取得
            total_schedules = len(schedule_manager.schedules)
            
            # UI更新
            if total_schedules == 1:
                message = f'録音がスケジュールされました。録音は、{self.stdt}に開始されます。'
            else:
                message = f'録音がスケジュールされました。録音は、{self.stdt}に開始されます。（{total_schedules}件の録音予約中）'
            
            try:
                notification_notify(
                    title='録音準備', 
                    message=message, 
                    app_name='rpb', 
                    timeout=10
                )
                self.log.info(f"Recording schedule notification sent successfully: {program_title}")
            except Exception as e:
                self.log.error(f"Failed to send recording schedule notification: {e}")
            
            self.log.info(f"Recording scheduled successfully: {program_title}")
            # ダイアログを閉じてメイン画面に戻る
            self.Destroy()
            return

        except Exception as e:
            #raise e
            self.log.error(f"Error in onFinishButton: {e}")
            simpleDialog.errorDialog(f"録音スケジュールに失敗しました: {e}")

    def onPlayTimeFree(self, event):
        """選択番組の聴き逃し再生/停止をトグル"""
        try:
            main_view = globalVars.app.hMainView
            if self._is_timefree_playing():
                main_view.radio_manager.stop_timefree()
                self._sync_seek_slider_from_player()
                self._update_timefree_button_label()
                return

            idx = self.lst.GetFocusedItem()
            title, start_dt, end_dt = self._get_selected_program_range()
            now = datetime.datetime.now()
            if start_dt > now:
                simpleDialog.errorDialog("未来の番組は聴き逃し再生できません。")
                return
            if end_dt > now:
                simpleDialog.errorDialog("この番組はまだ放送中のため、聴き逃し配信が利用できません。")
                return
            announce = f"聴き逃し再生: {self.radioname} {title}"
            duration_sec = int(max(1, (end_dt - start_dt).total_seconds()))
            performer = self.pfmlst[idx] if 0 <= idx < len(self.pfmlst) else ""
            description = self.dsclst[idx] if 0 <= idx < len(self.dsclst) else ""
            timefree_info = {
                "station_id": self.stid,
                "station_name": self.radioname,
                "title": title,
                "performer": performer,
                "description": description,
                "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "ft_dt": start_dt,
                "to_dt": end_dt,
                "stream_type": "b",
                "duration_sec": duration_sec,
            }
            # 優先: type=b
            try:
                stream_url, headers = self.progs.get_timefree_playback_source(self.stid, start_dt, end_dt)
                main_view.radio_manager.play_timefree(
                    stream_url,
                    station_id=self.stid,
                    announce_text=announce,
                    headers=headers,
                    resume_seconds=0,
                    timefree_info=timefree_info
                )
                self._current_timefree_duration_sec = duration_sec
                self._sync_seek_slider_from_player()
                self._start_seek_timer()
                self._update_timefree_button_label()
                return
            except Exception as e:
                self.log.warning(f"timefree playback primary source failed: {e}")

            # フォールバック: type=c
            stream_url, headers = self.progs.get_timefree_playback_source_compat(self.stid, start_dt, end_dt)
            main_view.radio_manager.play_timefree(
                stream_url,
                station_id=self.stid,
                announce_text=announce,
                headers=headers,
                resume_seconds=0,
                timefree_info={**timefree_info, "stream_type": "c"}
            )
            self._current_timefree_duration_sec = duration_sec
            self._sync_seek_slider_from_player()
            self._start_seek_timer()
            self._update_timefree_button_label()
        except Exception as e:
            self.log.error(f"Error in onPlayTimeFree: {e}")
            simpleDialog.errorDialog(f"聴き逃し再生に失敗しました: {e}")
            self._stop_seek_timer()
            self._update_timefree_button_label()

    def _start_seek_timer(self):
        if self._is_disposed or not self.timefree_seek_timer:
            return
        if not self.timefree_seek_timer.IsRunning():
            self.timefree_seek_timer.Start(1000)

    def _stop_seek_timer(self):
        if not self.timefree_seek_timer:
            return
        if self.timefree_seek_timer.IsRunning():
            self.timefree_seek_timer.Stop()
        if self.timefree_seek_apply_timer and self.timefree_seek_apply_timer.IsRunning():
            self.timefree_seek_apply_timer.Stop()

    def onTimefreeSeekTimer(self, event):
        if self._is_disposed:
            return
        self._sync_seek_slider_from_player()

    def _sync_seek_slider_from_player(self):
        if self._is_disposed:
            return
        if not hasattr(self, "timefree_seek_slider"):
            return
        main_view = globalVars.app.hMainView
        if not hasattr(main_view, "radio_manager"):
            return
        radio_manager = main_view.radio_manager
        duration = radio_manager.get_timefree_duration_seconds() or self._current_timefree_duration_sec
        position = radio_manager.get_timefree_position_seconds()
        if duration <= 0:
            duration = max(position, 1)
        position = max(0, min(position, duration))
        self._updating_seek_ui = True
        try:
            self.timefree_seek_slider.SetRange(0, int(duration))
            self.timefree_seek_slider.SetValue(int(position))
            self.timefree_seek_label.SetLabel(
                f"{self._format_hhmmss(position)} / {self._format_hhmmss(duration)}"
            )
        except RuntimeError:
            # 破棄中/破棄後のウィジェット参照を無視
            self._stop_seek_timer()
            return
        finally:
            self._updating_seek_ui = False
        if self._is_timefree_playing():
            self._start_seek_timer()
        else:
            self._stop_seek_timer()

    def onTimefreeSeekChanged(self, event):
        if self._is_disposed or self._updating_seek_ui:
            return
        if not hasattr(globalVars.app.hMainView, "radio_manager"):
            return
        target = int(self.timefree_seek_slider.GetValue())
        # まず表示だけ即更新し、実seekはデバウンスして1回だけ適用
        duration = int(max(self.timefree_seek_slider.GetMax(), self._current_timefree_duration_sec, 1))
        self.timefree_seek_label.SetLabel(
            f"{self._format_hhmmss(target)} / {self._format_hhmmss(duration)}"
        )
        self._pending_seek_seconds = target
        if self.timefree_seek_apply_timer:
            self.timefree_seek_apply_timer.Start(120, oneShot=True)

    def onTimefreeSeekApplyTimer(self, event):
        if self._is_disposed:
            return
        if self._pending_seek_seconds is None:
            return
        self._start_seek_worker()

    def _start_seek_worker(self):
        if self._is_disposed:
            return
        with self._seek_worker_lock:
            if self._seek_worker_running:
                return
            self._seek_worker_running = True
        threading.Thread(target=self._seek_worker_loop, daemon=True).start()

    def _seek_worker_loop(self):
        error_message = None
        try:
            while not self._is_disposed:
                with self._seek_worker_lock:
                    target = self._pending_seek_seconds
                    self._pending_seek_seconds = None
                if target is None:
                    break
                try:
                    globalVars.app.hMainView.radio_manager.seek_timefree(int(target))
                except Exception as e:
                    self.log.error(f"Failed to seek timefree playback: {e}")
                    error_message = str(e)
                    break
            if not self._is_disposed:
                wx.CallAfter(self._sync_seek_slider_from_player)
                if error_message:
                    wx.CallAfter(simpleDialog.errorDialog, f"シークに失敗しました: {error_message}")
        finally:
            with self._seek_worker_lock:
                self._seek_worker_running = False
                needs_restart = self._pending_seek_seconds is not None and not self._is_disposed
            if needs_restart:
                wx.CallAfter(self._start_seek_worker)

    def _format_hhmmss(self, seconds):
        total = int(max(0, seconds))
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    def onRecordTimeFree(self, event):
        """選択番組を聴き逃し録音"""
        try:
            from recorder import recorder_manager, create_recording_dir
            filetype = self._refresh_selected_filetype()

            title, start_dt, end_dt = self._get_selected_program_range()
            now = datetime.datetime.now()
            if start_dt > now:
                simpleDialog.errorDialog("未来の番組は聴き逃し録音できません。")
                return
            if end_dt > now:
                simpleDialog.errorDialog("この番組はまだ放送中のため、聴き逃し録音は開始できません。")
                return

            duration_sec = int(max(1, (end_dt - start_dt).total_seconds()))
            stream_url, headers = self.progs.get_timefree_recording_source(self.stid, start_dt, end_dt)

            safe_title = re.sub(r'[<>:"/\\|?*]', '_', title).strip()
            replace = safe_title.replace(" ", "-")
            station_dir = self.radioname.replace(" ", "_")
            dirs = create_recording_dir(station_dir, title)
            timestamp = start_dt.strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(dirs, f"{timestamp}_{replace}")

            info = f"{self.radioname} {title}"
            end_time = time.time() + duration_sec + 30

            recorder = recorder_manager.start_recording(
                stream_url,
                output_path,
                info,
                end_time,
                filetype,
                station_id=self.stid,
                program_title=title,
                recording_seconds=duration_sec,
                http_headers=headers,
                input_options=["-http_seekable", "0", "-seekable", "0"]
            )
            if recorder:
                simpleDialog.dialog("完了", f"聴き逃し録音を開始しました。\n{title}")
            else:
                detail = recorder_manager.get_last_start_error()
                msg = "聴き逃し録音の開始に失敗しました。"
                if detail:
                    msg += f"\n\n{detail}"
                simpleDialog.errorDialog(msg)
        except Exception as e:
            self.log.error(f"Error in onRecordTimeFree: {e}")
            simpleDialog.errorDialog(f"聴き逃し録音に失敗しました: {e}")

    def _get_selected_program_range(self):
        """選択番組のタイトルと開始/終了日時を返す"""
        idx = self.lst.GetFocusedItem()
        if idx < 0:
            raise ValueError("番組を選択してください。")

        selection = self.cmb.GetSelection()
        if selection < 0 or selection >= len(self.date_values):
            raise ValueError("日付が選択されていません。")
        date_str = self.date_values[selection]
        if not date_str:
            raise ValueError("日付が選択されていません。")

        try:
            year, month, day = [int(v) for v in date_str.split("/")]
            base_date = datetime.date(year, month, day)
        except Exception as e:
            raise ValueError(f"日付の解析に失敗しました: {date_str} ({e})")

        title = self.lst.GetItemText(idx, 0)
        if not title:
            raise ValueError("番組タイトルを取得できませんでした。")

        start_text = self.lst.GetItemText(idx, 2)
        end_text = self.lst.GetItemText(idx, 3)

        start_dt = self._parse_program_time(base_date, start_text)
        end_dt = self._parse_program_time(base_date, end_text)
        if end_dt <= start_dt:
            end_dt += datetime.timedelta(days=1)
        return title, start_dt, end_dt

    def _parse_program_time(self, base_date, time_text):
        """番組時刻(24+時対応)をdatetimeに変換"""
        parts = time_text.split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid time format: {time_text}")
        hour = int(parts[0])
        minute = int(parts[1])
        day_offset = hour // 24
        hour = hour % 24
        target_date = base_date + datetime.timedelta(days=day_offset)
        return datetime.datetime.combine(target_date, datetime.time(hour, minute))

    def on_application_close(self, event):
        """アプリケーション終了時の処理"""
        try:
            self._cleanup_dialog_resources()
        except Exception as e:
            self.log.error(f"Error during application close: {e}")
        event.Skip()

    def _cleanup_dialog_resources(self):
        if self._is_disposed:
            return
        self._is_disposed = True
        self._stop_seek_timer()
        try:
            if self._main_window:
                self._main_window.Unbind(wx.EVT_CLOSE, handler=self.on_application_close)
        except Exception:
            pass

    def onCloseBtn(self, event):
        """閉じる操作時のクリーンアップ"""
        self._cleanup_dialog_resources()
        event.Skip()


    def InstallControls(self):
        """コントロールを配置"""
        super().InstallControls()

        # 録音予約/聴き逃し操作ボタン
        self.record_btn = self.creator.button(_("録音予約(&R)"), self.onFinishButton)
        self.timefree_play_btn = self.creator.button(_("聴き逃し再生(&P)"), self.onPlayTimeFree)
        self.timefree_record_btn = self.creator.button(_("聴き逃し録音(&T)"), self.onRecordTimeFree)
        self.timefree_seek_slider, self.timefree_seek_label = self.creator.slider(
            _("聴き逃しシーク"),
            min=0,
            max=1,
            defaultValue=0,
            event=self.onTimefreeSeekChanged,
            x=400,
            sizerFlag=wx.ALL | wx.EXPAND
        )
        # 音量スライダーと同様に、wx.Slider 標準のページ移動で操作する
        self.timefree_seek_slider.SetPageSize(10)
        self.timefree_seek_slider.Enable(False)
        self.timefree_seek_label.SetLabel("00:00:00 / 00:00:00")
        self._update_timefree_button_label()
        self._sync_seek_slider_from_player()
        self.wnd.Bind(wx.EVT_ACTIVATE, self.onDialogActivated)


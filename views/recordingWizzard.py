import wx
import time
import datetime
import re
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
        self._is_disposed = False
        self._main_window = globalVars.app.hMainView.hFrame
        super().Initialize()
        self.log = getLogger("recording_wizzard")
        self._main_window.Bind(wx.EVT_CLOSE, self.on_application_close)

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
            self.timefree_play_btn.Enable(True)
        if hasattr(main_view, "radio_manager"):
            main_view.radio_manager.update_timefree_command_ui()

    def onDialogActivated(self, event):
        """ダイアログ再アクティブ時にボタン状態を再同期"""
        if self._is_disposed:
            event.Skip()
            return
        self._update_timefree_button_label()
        event.Skip()

    def onFinishButton(self, event):
        """録音予約を確定"""
        try:
            self._refresh_selected_filetype()
            program_title, start_dt, end_dt = self._get_selected_program_range()

            self.stdt = start_dt
            self.endt = end_dt
            current = datetime.datetime.now()

            if self.stdt < current:
                simpleDialog.errorDialog("過去の番組の録音はできません。番組を選び直してください。")
                self.log.error(f"Failed to schedule program: Specified time ({self.stdt}) is in the past.")
                return
            
            safe_title = re.sub(r'[<>:"/\\|?*]', '_', program_title).strip()
            replace = safe_title.replace(" ", "-")
            from recorder import create_recording_dir
            station_dir = self.radioname.replace(" ", "_")
            dirs = create_recording_dir(station_dir, program_title)
            
            timestamp = self.stdt.strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(dirs, f"{timestamp}_{replace}")
            
            schedule = RecordingSchedule(
                station_id=self.stid,
                station_name=self.radioname,
                program_title=program_title,
                start_time=self.stdt,
                end_time=self.endt,
                output_path=output_path,
                filetype=self.filetype
            )
            
            added = schedule_manager.add_schedule(schedule)
            if not added:
                simpleDialog.dialog(_("情報"), _("同一番組の予約が既に存在します。"))
                return
            self.current_schedule = schedule
            
            schedule_manager.start_monitoring()
            
            total_schedules = len(schedule_manager.schedules)
            
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
            self.Destroy()
            return

        except Exception as e:
            self.log.error(f"Error in onFinishButton: {e}")
            simpleDialog.errorDialog(f"録音スケジュールに失敗しました: {e}")

    def onPlayTimeFree(self, event):
        """選択番組の聴き逃し再生/停止をトグル"""
        try:
            main_view = globalVars.app.hMainView
            if self._is_timefree_playing():
                main_view.radio_manager.stop_timefree()
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
            if hasattr(main_view, "radio_manager") and main_view.radio_manager.is_live_playing():
                if simpleDialog.yesNoDialog(
                    _("確認"),
                    _("ライブ再生を終了し、聴き逃し再生を開始しますか？"),
                    self.wnd,
                ) != wx.ID_YES:
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
                self._update_timefree_button_label()
                return
            except Exception as e:
                self.log.warning(f"timefree playback primary source failed: {e}")

            stream_url, headers = self.progs.get_timefree_playback_source_compat(self.stid, start_dt, end_dt)
            main_view.radio_manager.play_timefree(
                stream_url,
                station_id=self.stid,
                announce_text=announce,
                headers=headers,
                resume_seconds=0,
                timefree_info={**timefree_info, "stream_type": "c"}
            )
            self._update_timefree_button_label()
        except Exception as e:
            self.log.error(f"Error in onPlayTimeFree: {e}")
            simpleDialog.errorDialog(f"聴き逃し再生に失敗しました: {e}")
            self._update_timefree_button_label()

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
        try:
            if self._main_window:
                self._main_window.Unbind(wx.EVT_CLOSE, handler=self.on_application_close)
        except Exception:
            pass

    def onCloseBtn(self, event):
        """閉じる操作時のクリーンアップ"""
        self._cleanup_dialog_resources()
        event.Skip()

    def on_program_list_selection(self, event):
        """過去番組行を選んだとき、ライブ×別局時のメニュー再開許可フラグを更新"""
        self._notify_past_program_focus_if_applicable()
        event.Skip()

    def _notify_past_program_focus_if_applicable(self):
        """現在フォーカスが「過去の放送済み番組」なら聴き逃しメニュー用 ACK を立てる"""
        try:
            idx = self.lst.GetFocusedItem()
            if idx < 0:
                return
            _, start_dt, end_dt = self._get_selected_program_range()
            now = datetime.datetime.now()
            if start_dt > now or end_dt > now:
                return
            main_view = globalVars.app.hMainView
            if not hasattr(main_view, "radio_manager"):
                return
            rm = main_view.radio_manager
            rm.set_timefree_menu_live_ack(self.stid)
            rm.update_timefree_command_ui()
        except Exception:
            pass

    def InstallControls(self):
        """コントロールを配置"""
        super().InstallControls()

        self.lst.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_program_list_selection)
        wx.CallAfter(self._notify_past_program_focus_if_applicable)

        self.record_btn = self.creator.button(_("録音予約(&R)"), self.onFinishButton)
        self.timefree_play_btn = self.creator.button(_("聴き逃し再生(&P)"), self.onPlayTimeFree)
        self.timefree_record_btn = self.creator.button(_("聴き逃し録音(&T)"), self.onRecordTimeFree)
        self._update_timefree_button_label()
        self.wnd.Bind(wx.EVT_ACTIVATE, self.onDialogActivated)


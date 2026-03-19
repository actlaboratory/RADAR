import wx
import time
import datetime
import locale
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
        super().Initialize()
        self.log = getLogger("recording_wizzard")
        main_window = globalVars.app.hMainView.hFrame
        main_window.Bind(wx.EVT_CLOSE, self.on_application_close)
        from recorder import get_file_type_from_config
        self.filetype = get_file_type_from_config()
        self.current_schedule = None

    def get_streamUrl(self, stationid):
        """ストリームURLを取得"""
        try:
            return self.progs.get_authenticated_stream_url(stationid)
        except Exception as e:
            self.log.error(f"Failed to get stream URL: {e}")

    def onFinishButton(self, event):
        """録音予約を確定"""
        try:
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
        """選択番組を聴き逃し再生"""
        try:
            title, start_dt, end_dt = self._get_selected_program_range()
            now = datetime.datetime.now()
            if start_dt > now:
                simpleDialog.errorDialog("未来の番組は聴き逃し再生できません。")
                return
            if end_dt > now:
                simpleDialog.errorDialog("この番組はまだ放送中のため、聴き逃し配信が利用できません。")
                return
            main_view = globalVars.app.hMainView
            announce = f"聴き逃し再生: {self.radioname} {title}"
            # 優先: type=c
            try:
                stream_url, headers = self.progs.get_timefree_playback_source(self.stid, start_dt, end_dt)
                main_view.radio_manager.play_timefree(
                    stream_url,
                    station_id=self.stid,
                    announce_text=announce,
                    headers=headers
                )
                return
            except Exception as e:
                self.log.warning(f"timefree playback primary source failed: {e}")

            # フォールバック: type=b
            stream_url, headers = self.progs.get_timefree_playback_source_compat(self.stid, start_dt, end_dt)
            main_view.radio_manager.play_timefree(
                stream_url,
                station_id=self.stid,
                announce_text=announce,
                headers=headers
            )
        except Exception as e:
            self.log.error(f"Error in onPlayTimeFree: {e}")
            simpleDialog.errorDialog(f"聴き逃し再生に失敗しました: {e}")

    def onRecordTimeFree(self, event):
        """選択番組を聴き逃し録音"""
        try:
            from recorder import recorder_manager, create_recording_dir

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
                self.filetype,
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

        date_str = self.clutl.getDateValue()[self.selection]
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
            # アプリケーション終了時は特に何もしない
            pass
        except Exception as e:
            self.log.error(f"Error during application close: {e}")
        event.Skip()


    def InstallControls(self):
        """コントロールを配置"""
        super().InstallControls()

        # 録音予約/聴き逃し操作ボタン
        self.record_btn = self.creator.button(_("録音予約(&R)"), self.onFinishButton)
        self.timefree_play_btn = self.creator.button(_("聴き逃し再生(&P)"), self.onPlayTimeFree)
        self.timefree_record_btn = self.creator.button(_("聴き逃し録音(&T)"), self.onRecordTimeFree)


import wx
import time
import datetime
import locale
from logging import getLogger
from plyer import notification
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
        self.filetype = "mp3"  # 設定から取得する場合は修正
        self.current_schedule = None

    def get_streamUrl(self, stationid):
        """ストリームURLを取得"""
        try:
            url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
            return self.progs.gettoken.gen_temp_chunk_m3u8_url(url, self.progs.token)
        except Exception as e:
            self.log.error(f"Failed to get stream URL: {e}")

    def onFinishButton(self, event):
        """録音予約を確定"""
        try:
            # 選択された日付と時間を取得
            date_str = self.clutl.getDateValue()[self.selection]
            if not date_str:
                simpleDialog.errorDialog("日付が選択されていません。")
                self.log.error("No date selected")
                return
                
            # 日付文字列を安全にパース
            try:
                selected_date = datetime.datetime.strptime(date_str.replace("/", "-"), "%Y-%m-%d")
            except ValueError as e:
                self.log.error(f"Date parsing error: {e}, date_str: {date_str}")
                simpleDialog.errorDialog(f"日付の解析に失敗しました: {date_str}")
                return
                
            current = datetime.datetime.now()
            
            # 開始・終了時間を取得
            start_time = self.lst.GetItemText(self.lst.GetFocusedItem(), 2)
            end_time = self.lst.GetItemText(self.lst.GetFocusedItem(), 3)
            
            # 24時間を超える場合の処理
            if int(start_time[:2]) >= 24:
                start_time = f"0{int(start_time[:2])-24}:{start_time[3:]}"
            if int(end_time[:2]) >= 24:
                end_time = f"0{int(end_time[:2])-24}:{end_time[3:]}"
            
            # 日時オブジェクトを作成
            try:
                start_time_dt = datetime.datetime.strptime(start_time, "%H:%M")
                end_time_dt = datetime.datetime.strptime(end_time, "%H:%M")
            except ValueError as e:
                self.log.error(f"Time parsing error: {e}, start_time: {start_time}, end_time: {end_time}")
                simpleDialog.errorDialog(f"時間の解析に失敗しました: {start_time} - {end_time}")
                return
            
            self.stdt = start_time_dt.replace(
                year=selected_date.year, 
                month=selected_date.month, 
                day=selected_date.day
            )
            self.endt = end_time_dt.replace(
                year=selected_date.year, 
                month=selected_date.month, 
                day=selected_date.day
            )
            
            # 日付の調整（深夜番組の場合）
            if self.stdt.time() < datetime.time(4, 59, 59):
                self.stdt += datetime.timedelta(days=1)
            if self.endt.time() <= datetime.time(5, 0):
                self.endt += datetime.timedelta(days=1)
            
            # 過去の番組かチェック
            if self.stdt < current:
                simpleDialog.errorDialog("過去の番組の録音はできません。番組を選び直してください。")
                self.log.error(f"Failed to schedule program: Specified time ({self.stdt}) is in the past.")
                return
            
            # 番組タイトルを取得
            program_title = self.lst.GetItemText(self.lst.GetFocusedItem(), 0)
            if not program_title:
                simpleDialog.errorDialog("番組タイトルを取得できませんでした。")
                self.log.error("Failed to get program title")
                return
            
            # 出力パスを準備
            replace = program_title.replace(" ", "-")
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
                notification.notify(
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
        
        # 録音予約ボタンを追加
        self.record_btn = self.creator.button(_("録音予約(&R)"), self.onFinishButton)
        


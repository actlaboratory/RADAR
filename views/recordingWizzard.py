import wx
from views import showRadioProgramScheduleListBase
import globalVars
import locale
import recorder
import simpleDialog
from views import token
import views.ViewCreator
from views import programmanager
from logging import getLogger
from plyer import notification
from views.baseDialog import *
import tcutil
import datetime

class RecordingWizzard(showRadioProgramScheduleListBase.ShowSchedule):
    def __init__(self, stid, radioname):
        super().__init__(stid, radioname)
        self.config = globalVars.app.config
        self.stid = stid
        self.radioname = radioname
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
        super().Initialize()
        self.recorder = recorder.Recorder()
        #タイマーオブジェクトをインスタンス化
        self.starttimer = wx.Timer()
        self.endtimer = wx.Timer()
        main_window = globalVars.app.hMainView.hFrame
        main_window.Bind(wx.EVT_CLOSE, self.on_application_close)

    def getFileType(self, id):
        """メニューidを受取.mp3か.wavを判断して返す"""
        self.recorder.setFileType(id)

    def get_streamUrl(self, stationid):
        url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
        self.m3u8 = self.progs.gettoken.gen_temp_chunk_m3u8_url( url ,self.progs.token)

    def init(self):
        self.log.debug("created")
        self.fnh = self.creator.okbutton(_("完了(&F)"), self.onFinishButton)
        return True

    def onFinishButton(self, event):
        try:
            locale.setlocale(locale.LC_TIME, 'ja_JP')
        except locale.Error:
            locale.setlocale(locale.LC_TIME, 'C')
        selected_date = datetime.datetime.strptime(self.clutl.getDateValue()[self.selection].replace("/", "-"), "%Y-%m-%d") #ユーザーが選択した
        current = datetime.datetime.now() #現在の日付
        #開始時間と終了時間を取得
        start_time = self.lst.GetItemText(self.lst.GetFocusedItem(), 2)
        #24次以降の番組の時間処理
        if int(start_time[:2]) >= 24:
            start_time =  f"0{int(start_time[:2])-24}:{start_time[3:]}"
        end_time = self.lst.GetItemText(self.lst.GetFocusedItem(), 3)
        if int(end_time[:2]) >= 24:
            end_time =  f"0{int(end_time[:2])-24}:{end_time[3:]}"

        #datetimeオブジェクトに変換
        start_time_dt = datetime.datetime.strptime(start_time, "%H:%M")
        end_time_dt = datetime.datetime.strptime(end_time, "%H:%M")

        self.stdt = start_time_dt.replace(year=selected_date.year, month=selected_date.month, day=selected_date.day)
        self.endt = end_time_dt.replace(year=selected_date.year, month=selected_date.month, day=selected_date.day)
        # 開始時間または終了時間が00:00から05:00の間の場合、日付を明日に変更
        if self.stdt.time() < datetime.time(4, 59, 59):
            self.stdt += datetime.timedelta(days=1)
        if self.endt.time() <= datetime.time(5, 0):
            self.endt += datetime.timedelta(days=1)
        time_until_start = (self.stdt - current).total_seconds() * 1000
        time_until_end = (self.endt - current).total_seconds() * 1000
        #過去の番組をスケジュールしようとした
        if time_until_start < 0:
            simpleDialog.errorDialog(_("過去の番組の録音をスケジュールすることはできません。番組を選び直してください。"))
            self.log.error(f"Failed to schedule program: Specified time ({self.stdt}) is in the past. Please select a future time.")
            return
        self.starttimer.StartOnce(int(time_until_start))
        self.endtimer.StartOnce(int(time_until_end))
        notification.notify(title='録音準備', message=f'録音がスケジュールされました。録音は、{self.stdt}に開始されます。', app_name='rpb', app_icon='', timeout=10, ticker='', toast=False)
        #メニュー名を変更しておく
        globalVars.app.hMainView.menu.SetMenuLabel("RECORDING_SCHEDULE", _("予約録音を取り消し(&S)"))

        self.starttimer.Bind(wx.EVT_TIMER, self.onStartTimer)
        self.endtimer.Bind(wx.EVT_TIMER, self.onEndTimer)
        self.log.info("The recording was scheduled successfully!")
        event.Skip()
        return

    def onStartTimer(self, event):
        self.progs.getArea() #トークン取得
        self.get_streamUrl(self.stid)

        title = self.progs.getNowProgram(self.stid)
        replace = title.replace(" ","-")
        #放送局名で作成されたディレクトリ名に使用不能な文字が含まれていたら置き換える
        dirs = self.recorder.create_recordingDir(self.radioname.replace(" ", "_"))
        self.recorder.record(self.m3u8, f"{dirs}\{str(datetime.date.today()) + replace}") #datetime+番組タイトルでファイル名を決定
        self.log.debug("timer is started")
        notification.notify(title='番組録音開始!', message='スケジュールされた番組の録音を開始しました。', app_name='rpb', app_icon='', timeout=10, ticker='', toast=False)
        globalVars.app.hMainView.menu.SetMenuLabel("RECORDING_SCHEDULE", _("録音を中止(&S)"))

    def onEndTimer(self, event):
        self.stop()
        self.log.debug("timer is stoped")

    def stop(self):
        self.recorder.stop_record()
        self.log.info("Scheduled recording was cancelled by the user!")
        self.cleanup_timers()

    def cleanup_timers(self):
        """Properly stop and destroy timers"""
        if self.starttimer:
            self.starttimer.Stop()
            self.starttimer = None
        if self.endtimer:
            self.endtimer.Stop()
            self.endtimer = None

    def on_application_close(self, event):
        """アプリケーションが終了された場合適切に処理を中断する"""
        self.stop()
        event.Skip()
        return

    def get_start_timer_status(self):
        return self.starttimer.IsRunning()

    def get_end_timer_status(self):
        return self.endtimer.IsRunning()


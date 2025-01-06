import wx
import globalVars
import ConfigManager
import locale
import recorder
import simpleDialog
from views import token
import views.ViewCreator
from views import programmanager
from logging import getLogger
from plyer import notification
from views.baseDialog import *
import itertools
import tcutil
import datetime

class RecordingWizzard(BaseDialog):
    def __init__(self, stid, radioname):
        super().__init__("recordingWizzardDialog")
        self.starttimer = wx.Timer()
        self.endtimer = wx.Timer()
        self.config = globalVars.app.config
        self.config["recording"]["recording_schedule"] = "INACTIVE" #動作していない
        self.stid = stid
        self.radioname = radioname
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
        self.recorder = recorder.Recorder()

    def getFileType(self, id):
        """メニューidを受取.mp3か.wavを判断して返す"""
        self.recorder.setFileType(id)

    def get_streamUrl(self, stationid):
        url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
        self.m3u8 = self.progs.gettoken.gen_temp_chunk_m3u8_url( url ,self.progs.token)

    def Initialize(self):
        self.log.debug("created")
        super().Initialize(self.app.hMainView.hFrame,_("予約録音ウィザード"))
        self.InstallControls()
        return True

    def InstallControls(self):
        """いろんなウィジェットを設置する"""
        self.creator=views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.sizer,wx.VERTICAL,20,style=wx.EXPAND|wx.ALL,margin=20)
        self.lst,programlist = self.creator.virtualListCtrl(_("録音する番組を選択してください"))
        self.lst.AppendColumn(_("タイトル"))
        self.lst.AppendColumn(_("出演者"))
        self.lst.AppendColumn(_("開始時間"))
        self.lst.AppendColumn(_("終了時間"))
        self.calendarSelector()
        self.lst.Focus(0)
        self.lst.Select(0)
        self.fnh = self.creator.okbutton(_("完了(&F)"), self.onFinishButton)
        self.cancel = self.creator.cancelbutton(_("キャンセル(&C)"), None)
        self.cancel.SetDefault()

    def calendarSelector(self):
        """日時指定用コンボボックスを作成し、内容を設定"""
        self.cmb,label = self.creator.combobox(_("日時を指定"), self.clutl.getDateValue())
        self.cmb.SetSelection(0)
        self.cmb.Bind(wx.EVT_COMBOBOX, self.show_programlist)
        # 初期状態を反映するために明示的にイベントを発生させる
        event = wx.CommandEvent(wx.EVT_COMBOBOX.typeId, self.cmb.GetId())
        event.SetInt(0)
        self.cmb.ProcessEvent(event)

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
        print(time_until_start)
        print(time_until_end)
        #過去の番組をスケジュールしようとした
        if time_until_start < 0:
            simpleDialog.errorDialog(_("過去の番組の録音をスケジュールすることはできません。番組を選び直してください。"))
            self.log.error(f"Failed to schedule program: Specified time ({self.stdt}) is in the past. Please select a future time.")
            return
        self.starttimer.StartOnce(int(time_until_start))
        self.endtimer.StartOnce(int(time_until_end))
        self.config["record"]["recording_schedule"] = "READY" #録音準備状態
        notification.notify(title='録音準備', message=f'録音がスケジュールされました。録音は、{self.stdt}に開始されます。', app_name='rpb', app_icon='', timeout=10, ticker='', toast=False)
        self.log.debug("The recording was scheduled successfully!")
        self.starttimer.Bind(wx.EVT_TIMER, self.onStartTimer)
        self.endtimer.Bind(wx.EVT_TIMER, self.onEndTimer)
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
        self.config["record"]["recording_schedule"] = "RUNNING" #予約録音実行中
        self.log.debug("timer is started")
        notification.notify(title='番組録音開始!', message='スケジュールされた番組の録音を開始しました。', app_name='rpb', app_icon='', timeout=10, ticker='', toast=False)

    def onEndTimer(self, event):
        self.stop()
        self.log.debug("timer is stoped")

    def stop(self):
        if self.config.getstring("recording", "recording_schedule") == "RUNNING":
            self.recorder.stop_record()
            self.log.info("Scheduled recording was cancelled by the user!")
        self.starttimer.Stop()
        self.endtimer.Stop()
        self.config["record"]["recording_schedule"] = "INACTIVE" #デフォル状態に戻す

    def show_programlist(self, event):
        self.lst.clear()
        selection = self.cmb.GetSelection()
        self.selection = selection
        if selection == None:
            return
        date = self.clutl.transform_date(self.clutl.getDateValue()[selection])
        self.progs.retrieveRadioListings(self.stid,date)
        title = self.progs.gettitle() #番組のタイトル
        pfm = self.progs.getpfm() #出演者の名前
        program_ftl = self.progs.get_ftl()
        program_tol = self.progs.get_tol()
        for t,p,ftl,tol in zip(title,pfm,program_ftl,program_tol):
            self.lst.Append((t,p, ftl[:2]+":"+ftl[2:4],tol[:2]+":"+tol[2:4]), )

    def get_start_timer_status(self):
        return self.starttimer.IsRunning()

    def get_end_timer_status(self):
        return self.endtimer.IsRunning()


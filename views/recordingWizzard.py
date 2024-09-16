import wx
import ConfigManager
import locale
import recorder
import simpleDialog
from views import SimpleInputDialog
from views import token
import views.ViewCreator
from views import programmanager
from logging import getLogger
from plyer import notification
from views.baseDialog import *
import itertools
import tcutil
import datetime
import winsound

class RecordingWizzard(BaseDialog):

    def __init__(self, stid, radioname):
        super().__init__("recordingWizzardDialog")
        self.config = ConfigManager.ConfigManager()
        self.stid = stid
        self.radioname = radioname
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
        self.recorder = recorder.Recorder()
        self.calendar()

    def get_streamUrl(self, stationid):
        url = f'http://f-radiko.smartstream.ne.jp/{stationid}/_definst_/simul-stream.stream/playlist.m3u8'
        self.m3u8 = self.progs.gettoken.gen_temp_chunk_m3u8_url( url ,self.progs.token)

    def Initialize(self):
        self.log.debug("created")
        super().Initialize(self.app.hMainView.hFrame,_("予約録音ウィザード"))
        self.InstallControls()
        return True

    def calendar(self):
        self.calendar_lists = list(itertools.chain.from_iterable(self.clutl.getMonth())) #２次元リストを一次元に変換
        del self.calendar_lists[0:3]
        del self.calendar_lists[-1]

    def calendarSelector(self):
        """日時指定用コンボボックスを作成し、内容を設定"""
        self.calst = []
        year = self.clutl.year
        month = self.clutl.month
        day = datetime.datetime.now().day
        del self.calendar_lists[0:self.calendar_lists.index(int(day))]
        for cal in self.calendar_lists:
            if len(str(cal)) < 2:
                self.calst.append(f"{year}/{month}/0{cal}")
            else:
                self.calst.append(f"{year}/{month}/{cal}")

    def InstallControls(self):
        """いろんなウィジェットを設置する"""
        self.calendarSelector()
        self.creator=views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.sizer,wx.VERTICAL,20,style=wx.EXPAND|wx.ALL,margin=20)
        self.lst,programlist = self.creator.virtualListCtrl(_("録音する番組を選択してください"))
        self.lst.AppendColumn(_("タイトル"))
        self.lst.AppendColumn(_("出演者"))
        self.lst.AppendColumn(_("開始時間"))
        self.lst.AppendColumn(_("終了時間"))
        self.show_programlist()
        self.fnh = self.creator.okbutton(_("完了(&F)"), self.onFinishButton)
        self.cancel = self.creator.cancelbutton(_("キャンセル(&C)"))

    def show_programlist(self):
        self.lst.clear()
        dt = datetime.datetime.now()
        date = dt.strftime("%Y%m%d")

        self.progs.retrieveRadioListings(self.stid, date)
        title = self.progs.gettitle() #番組のタイトル
        pfm = self.progs.getpfm() #出演者の名前
        program_ftl = self.progs.get_ftl()
        program_tol = self.progs.get_tol()
        for t,p,ftl,tol in zip(title,pfm,program_ftl,program_tol):
            self.lst.Append((t,p, ftl[:2]+":"+ftl[2:4],tol[:2]+":"+tol[2:4]), )

    def onFinishButton(self, event):
        try:
            locale.setlocale(locale.LC_TIME, 'ja_JP')
        except locale.Error:
            locale.setlocale(locale.LC_TIME, 'C')
        now = datetime.datetime.now()
        self.starttimer = wx.Timer()
        self.endtimer = wx.Timer()
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
        # 日付を今日の日付に設定
        self.stdt = start_time_dt.replace(year=now.year, month=now.month, day=now.day)
        self.endt = end_time_dt.replace(year=now.year, month=now.month, day=now.day)
        # 開始時間または終了時間が00:00から05:00の間の場合、日付を明日に変更
        if self.stdt.time() < datetime.time(4, 59, 59):
            self.stdt += datetime.timedelta(days=1)
        if self.endt.time() <= datetime.time(5, 0):
            self.endt += datetime.timedelta(days=1)

        time_until_start = (self.stdt - now).total_seconds() * 1000
        time_until_end = (self.endt - now).total_seconds() * 1000
        #過去の番組をスケジュールしようとした
        if time_until_start < 0:
            simpleDialog.errorDialog(_("過去の番組の録音をスケジュールすることはできません。番組を選び直してください。"))
            self.log.error("Recording schedule failed!")
            return
        self.starttimer.StartOnce(int(time_until_start))
        self.endtimer.StartOnce(int(time_until_end))
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
        #放送局の名前でディレクトリを作成、スペースを除去しないと正しく保存されないので_に置き換える
        dirs = self.recorder.create_recordingDir(self.radioname.replace(" ", "_"))
        self.recorder.setFileType(self.config.getint("recording", "menu_id"))
        self.recorder.record(self.m3u8, f"{dirs}\{str(datetime.date.today()) + replace}") #datetime+番組タイトルでファイル名を決定

    def onEndTimer(self, event):
        self.recorder.stop_record()
        self.starttimer.Stop()



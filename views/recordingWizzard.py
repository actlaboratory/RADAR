import wx
import locale
import recorder
import simpleDialog

import views.ViewCreator
from views import programmanager
from logging import getLogger
from views.baseDialog import *
import itertools
import tcutil
import datetime

class RecordingWizzard(BaseDialog):
    def __init__(self, stid):
        super().__init__("recordingWizzardDialog")
        self.stid = stid
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
        self.calendar()

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
        self.cmb,label = self.creator.combobox(_("日時指定"), self.calst)
        self.lst,programlist = self.creator.virtualListCtrl(_("録音する番組を選択してください"))
        self.lst.AppendColumn(_("タイトル"))
        self.lst.AppendColumn(_("出演者"))
        self.lst.AppendColumn(_("開始時間"))
        self.lst.AppendColumn(_("終了時間"))
        self.fnh = self.creator.okbutton(_("完了(&F)"), self.onFinishButton)
        self.cancel = self.creator.cancelbutton(_("キャンセル(&C)"))
        self.cmb.Bind(wx.EVT_COMBOBOX, self.show_programlist)

    def show_programlist(self, event):
        self.lst.clear()
        selection = self.cmb.GetSelection()
        if selection == None:
            return
        date = self.clutl.dateToInteger(self.calst[selection])
        self.progs.retrieveRadioListings(self.stid,date)
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
        self.timer = wx.Timer()
        #開始時間と終了時間を取得
        start_time = self.lst.GetItemText(self.lst.GetFocusedItem(), 2)
        end_time = self.lst.GetItemText(self.lst.GetFocusedItem(), 3)
        #datetimeオブジェクトに変換
        start_time_dt = datetime.datetime.strptime(start_time, "%H:%M")
        end_time_dt = datetime.datetime.strptime(end_time, "%H:%M")
        #y/m/d h:mの形にしてインスタンス変数に格納
        self.stdt = start_time_dt.replace(year=now.year, month=now.month, day=now.day)
        self.endt = end_time_dt.replace(year=now.year, month=now.month, day=now.day)
        if self.stdt < now:
            simpleDialog.errorDialog(_("過去の番組を予約することはできません。\n番組を選び直してください。"))
            return
        self.timer.start(1000)
        self.timer.Bind(wx.EVT_TIMER, self.onTimer)
        event.Skip()
        return

    def onTimer(self, event):
        now = datetime.datetime.now().time()

    def __del__(self):
        """デストラクタ"""
        #self.timer.stop()
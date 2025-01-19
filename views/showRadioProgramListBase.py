
import wx
import globalVars
import ConfigManager
from views import token
import views.ViewCreator
from views import programmanager
from logging import getLogger
from views.baseDialog import *
import tcutil
import datetime

class ShowSchedule(BaseDialog):
    def __init__(self, stid, radioname):
        super().__init__("ShowScheduleListBase")
        self.config = globalVars.app.config
        self.stid = stid
        self.radioname = radioname
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
    def Initialize(self):
        self.log.debug("created")
        super().Initialize(self.app.hMainView.hFrame,_("番組表"))
        self.InstallControls()
        return True

    def InstallControls(self):
        """いろんなウィジェットを設置する"""
        self.creator=views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.sizer,wx.VERTICAL,20,style=wx.EXPAND|wx.ALL,margin=20)
        self.lst,programlist = self.creator.virtualListCtrl(_("番組一覧"))
        self.lst.AppendColumn(_("タイトル"))
        self.lst.AppendColumn(_("出演者"))
        self.lst.AppendColumn(_("開始時間"))
        self.lst.AppendColumn(_("終了時間"))
        self.calendarSelector()
        self.lst.Focus(0)
        self.cls = self.creator.closebutton(_("閉じる(&C)"), self.onCloseBtn)
        self.cls.SetDefault()
        return

    def calendarSelector(self):
        """日時指定用コンボボックスを作成し、内容を設定"""
        self.cmb,label = self.creator.combobox(_("日時を指定"), self.clutl.getDateValue())
        self.cmb.SetSelection(0)
        self.cmb.Bind(wx.EVT_COMBOBOX, self.show_programlist)
        # 初期状態を反映するために明示的にイベントを発生させる
        event = wx.CommandEvent(wx.EVT_COMBOBOX.typeId, self.cmb.GetId())
        event.SetInt(0)
        self.cmb.ProcessEvent(event)

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

    def onCloseBtn(self, event):
        event.Skip()
        return

    def show_detail(self, event):
        """番組詳細"""
        
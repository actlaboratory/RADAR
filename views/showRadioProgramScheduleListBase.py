
import wx
import globalVars
from views import token
import views.ViewCreator
from views import programmanager
from views import programdetail
from logging import getLogger
from views.baseDialog import *
import tcutil
import datetime
import re

class ShowSchedule(BaseDialog):
    def __init__(self, stid, radioname):
        super().__init__("ShowScheduleListBase")
        self.config = globalVars.app.config
        self.stid = stid
        self.radioname = radioname
        self.clutl = tcutil.CalendarUtil()
        self.progs = programmanager.ProgramManager()
        self.dsclst = []
        self.tilst = []
        self.pfmlst = []
        self.stlst = []
        self.enlst = []
        self.lst = None

    def Initialize(self):
        self.log.debug("created")
        super().Initialize(self.app.hMainView.hFrame,_("番組表"))
        self.InstallControls()
        return True

    def InstallControls(self):
        """いろんなウィジェットを設置する"""
        self.creator=views.ViewCreator.ViewCreator(self.viewMode,self.panel,self.sizer,wx.VERTICAL,20,style=wx.EXPAND|wx.ALL,margin=20)
        self.calendarSelector()

        self.lst,programlist = self.creator.virtualListCtrl(_("番組一覧"), size=(800,400), sizerFlag=wx.ALL|wx.EXPAND)
        self.lst.AppendColumn(_("タイトル"), 0, 380)
        self.lst.AppendColumn(_("出演者"), 0, 200)
        self.lst.AppendColumn(_("開始時間"),0,100)
        self.lst.AppendColumn(_("終了時間"),0,100)
        self.lst.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.show_detail)
        self.lst.Focus(0)
        self.lst.SetFocus()

        self.cls = self.creator.closebutton(_("閉じる(&C)"), self.onCloseBtn)
        self.cls.SetDefault()

        self.show_programlist()

    def calendarSelector(self):
        """日時指定用コンボボックスを作成し、内容を設定"""
        self.cmb,label = self.creator.combobox(_("日付指定"), self.clutl.getDateValue(), textLayout=wx.HORIZONTAL)
        self.cmb.SetSelection(0)
        self.cmb.Bind(wx.EVT_COMBOBOX, self.show_programlist)

    def show_programlist(self, event=None):
        self.lst.clear()
        self.dsclst.clear()
        self.tilst.clear()
        self.pfmlst.clear()
        self.stlst.clear()
        self.enlst.clear()
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
        description = self.progs.getDescriptions() #番組の説明
        for t,p,ftl,tol,d in zip(title,pfm,program_ftl,program_tol, description):
            self.lst.Append((t,p, ftl[:2]+":"+ftl[2:4],tol[:2]+":"+tol[2:4]), )
            if d:
                self.dsclst.append(re.sub(re.compile('<.*?>'), '', d))
            else:
                self.dsclst.append("説明無し")
            self.tilst.append(t)
            if p:
                self.pfmlst.append(p)
            else:
                self.pfmlst.append("")
            self.stlst.append(ftl[:2]+":"+ftl[2:4])
            self.enlst.append(tol[:2]+":"+tol[2:4])

    def onCloseBtn(self, event):
        event.Skip()
        return

    def show_detail(self, event):
        """番組詳細"""
        pd = programdetail.dialog()
        pd.show_dsc(self.dsclst, self.lst.GetFocusedItem())
        pd.show_title(self.tilst, self.lst.GetFocusedItem())
        pd.show_pfm(self.pfmlst, self.lst.GetFocusedItem())
        pd.show_starttime(self.stlst, self.lst.GetFocusedItem())
        pd.show_endtime(self.enlst, self.lst.GetFocusedItem())
        pd.Initialize()
        pd.Show()
        return

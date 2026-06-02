
import wx
import globalVars
import constants
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
        self.date_values = []
        self._default_date_values = []

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

        self.cls = self.creator.closebutton(_("閉じる(&C)"), self.onCloseBtn)
        self.cls.SetDefault()

        self.show_programlist()

    def calendarSelector(self):
        """日時指定用コンボボックスを作成し、内容を設定"""
        self._default_date_values = list(self.clutl.getDateValue())
        self.date_values = list(self._default_date_values)
        row_creator = views.ViewCreator.ViewCreator(
            self.viewMode,
            self.creator.GetPanel(),
            self.creator.GetSizer(),
            wx.HORIZONTAL,
            style=wx.ALL,
            margin=5
        )
        self.cmb, label = row_creator.combobox(_("日付指定"), self.date_values, textLayout=wx.HORIZONTAL)
        self.cmb.SetSelection(0)
        self._date_combo_dropdown_used = False
        self.cmb.Bind(wx.EVT_COMBOBOX_DROPDOWN, self._on_date_combo_dropdown)
        self.cmb.Bind(wx.EVT_COMBOBOX_CLOSEUP, self._on_date_combo_closeup)
        self.cmb.Bind(wx.EVT_COMBOBOX, self.show_programlist)
        row_creator.AddSpace(20)
        self.show_past_chk = row_creator.checkbox(
            _("過去1週間の日付を表示"),
            event=self.onTogglePastProgramDates
        )
        self.show_past_chk.SetValue(constants.SHOW_PAST_WEEK_DATES_DEFAULT)
        self._rebuild_date_values(self.show_past_chk.GetValue())

    def _get_radio_base_date(self):
        """ラジオ日付ルール(5時切替)に従った基準日を返す"""
        now = datetime.datetime.now()
        if now.hour < 5:
            return now.date() - datetime.timedelta(days=1)
        return now.date()

    def _build_past_dates(self):
        base_date = self._get_radio_base_date()
        past_dates = []
        for days in range(7, 0, -1):
            d = base_date - datetime.timedelta(days=days)
            date_str = f"{d.year}/{d.month}/{d.day}"
            if date_str not in self._default_date_values:
                past_dates.append(date_str)
        return past_dates

    def _rebuild_date_values(self, include_past):
        selected_value = None
        current_selection = self.cmb.GetSelection()
        if 0 <= current_selection < len(self.date_values):
            selected_value = self.date_values[current_selection]

        if include_past:
            merged_dates = self._build_past_dates() + list(self._default_date_values)
        else:
            merged_dates = list(self._default_date_values)

        self.date_values = merged_dates
        self.cmb.SetItems(self.date_values)

        if selected_value and selected_value in self.date_values:
            self.cmb.SetSelection(self.date_values.index(selected_value))
        else:
            self.cmb.SetSelection(0 if self.date_values else -1)

    def onTogglePastProgramDates(self, event):
        include_past = self.show_past_chk.GetValue()
        self._rebuild_date_values(include_past)
        self.show_programlist(focus_program_list=False)

    def _on_date_combo_dropdown(self, event):
        """ドロップダウン一覧を開いた操作（Alt+下矢印など）を記録する"""
        self._date_combo_dropdown_used = True
        event.Skip()

    def _on_date_combo_closeup(self, event):
        """一覧を閉じただけ（Esc 等）のときは、次の矢印操作を誤判定しない"""
        wx.CallAfter(self._reset_date_combo_dropdown_flag)
        event.Skip()

    def _reset_date_combo_dropdown_flag(self):
        self._date_combo_dropdown_used = False

    def show_programlist(self, event=None, focus_program_list=True):
        if event is not None:
            # 閉じた状態で上下矢印した場合はフォーカスを番組一覧へ移さない
            focus_program_list = self._date_combo_dropdown_used
            self._date_combo_dropdown_used = False
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
        if selection < 0 or selection >= len(self.date_values):
            return
        date = self.clutl.transform_date(self.date_values[selection])
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

        if focus_program_list:
            wx.CallAfter(self._ensure_first_program_row_focused)

    def _ensure_first_program_row_focused(self):
        """番組一覧の先頭行を選択し、リストにキーボードフォーカスを置く"""
        if self.lst is None:
            return
        try:
            if self.lst.GetItemCount() <= 0:
                return
        except Exception:
            return
        try:
            self.lst.Focus(0)
            self.lst.Select(0)
            self.lst.EnsureVisible(0)
            self.lst.SetFocus()
        except Exception:
            pass

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

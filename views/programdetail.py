import wx
import constants, update
from views import baseDialog, ViewCreator
class dialog(baseDialog.BaseDialog):
    def __init__(self):
        super().__init__("programDetailDialog")

    def Initialize(self):
        self.log.debug("created")
        super().Initialize(self.app.hMainView.hFrame,_("詳細情報"))
        self.InstallControls()
        return True

    def InstallControls(self):
        """いろんなwidgetを設置する。"""
        creator = ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 10, style=wx.ALL, margin=20)
        self.info, prodsc = creator.inputbox(_("番組説明"), defaultValue="\r\n".join(self.dsc), style=wx.TE_MULTILINE|wx.TE_READONLY | wx.TE_NO_VSCROLL | wx.BORDER_RAISED, sizerFlag=wx.EXPAND, x=750, textLayout=None)
        self.title, prottl = creator.inputbox(_("番組名"), defaultValue="\r\n".join(self.title), style=wx.TE_MULTILINE|wx.TE_READONLY | wx.TE_NO_VSCROLL | wx.BORDER_RAISED, sizerFlag=wx.EXPAND, x=750, textLayout=None)
        self.pfm, propfm = creator.inputbox(_("出演者"), defaultValue="\r\n".join(self.pfm), style=wx.TE_MULTILINE|wx.TE_READONLY | wx.TE_NO_VSCROLL | wx.BORDER_RAISED, sizerFlag=wx.EXPAND, x=750, textLayout=None)
        self.starttime, prostat = creator.inputbox(_("開始時間"), defaultValue="\r\n".join(self.st), style=wx.TE_MULTILINE|wx.TE_READONLY | wx.TE_NO_VSCROLL | wx.BORDER_RAISED, sizerFlag=wx.EXPAND, x=750, textLayout=None)
        self.endtime, proendt = creator.inputbox(_("終了時間"), defaultValue="\r\n".join(self.et), style=wx.TE_MULTILINE|wx.TE_READONLY | wx.TE_NO_VSCROLL | wx.BORDER_RAISED, sizerFlag=wx.EXPAND, x=750, textLayout=None)
        f = self.info.GetFont()
        f.SetPointSize((int)(f.GetPointSize() * (2/3)))
        self.info.SetFont(f)
        self.info.SetMinSize(wx.Size(750,240))

        # フッター
        footerCreator = ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, style=wx.ALIGN_RIGHT | wx.ALL, margin=20)
        self.closeBtn = footerCreator.closebutton(_("閉じる"))
        self.closeBtn.SetDefault()

    def show_dsc(self, description, ix):
        """番組詳細情報をテキストボックスに追加する"""
        self.dsc = []
        self.dsc.append(description[ix])

    def show_title(self, title, ix):
        """番組詳細情報をテキストボックスに追加する"""
        self.title = []
        self.title.append(title[ix])

    def show_pfm(self, pfm, ix):
        """番組詳細情報をテキストボックスに追加する"""
        self.pfm = []
        self.pfm.append(pfm[ix])

    def show_starttime(self, stdt, ix):
        """番組詳細情報をテキストボックスに追加する"""
        self.st = []
        self.st.append(stdt[ix])

    def show_endtime(self, endt, ix):
        """番組詳細情報をテキストボックスに追加する"""
        self.et = []
        self.et.append(endt[ix])

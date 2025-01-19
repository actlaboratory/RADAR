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
        textList = []
        creator = ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, wx.VERTICAL, 10, style=wx.ALL, margin=20)
        self.info, dummy = creator.inputbox("番組説明", defaultValue="\r\n".join(textList), style=wx.TE_MULTILINE|wx.TE_READONLY | wx.TE_NO_VSCROLL | wx.BORDER_RAISED, sizerFlag=wx.EXPAND, x=750, textLayout=None)
        f = self.info.GetFont()
        f.SetPointSize((int)(f.GetPointSize() * (2/3)))
        self.info.SetFont(f)
        self.info.SetMinSize(wx.Size(750,240))


        # フッター
        footerCreator = ViewCreator.ViewCreator(self.viewMode, self.panel, self.sizer, style=wx.ALIGN_RIGHT | wx.ALL, margin=20)
        self.closeBtn = footerCreator.closebutton(_("閉じる"))
        self.closeBtn.SetDefault()

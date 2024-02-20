
import re

def getValueString(ref_id):
	""" ナビキーとダイアログ文字列を消去した文字列を取り出し """
	dicVal = dic[ref_id]
	s = re.sub("\.\.\.$", "", dicVal)
	s = re.sub("\(&.\)$", "", s)
	return re.sub("&", "", s)

dic={
	"FILE_EXAMPLE":_("テストダイアログを閲覧")+"...",
	"FILE_EXIT": _("終了"),

	"FUNCTION_PLAY_PLAY":_("再生"),
	"FUNCTION_PLAY_MUTE":_("ミュート"),
	"FUNCTION_VOLUME_UP":_("音量を上げる"),
	"FUNCTION_VOLUME_DOWN":_("音量を下げる"),

	"SHOW_NOW_PROGRAMLIST":_("選択された放送局の今日の番組表を表示"),
	"SHOW_WEEK_PROGRAMLIST":_("習慣番組表の表示"),
	"HIDE_PROGRAMINFO":_("番組情報を非表示"),

	"OPTION_OPTION":_("オプション(&O)")+"...",
	"OPTION_KEY_CONFIG":_("ショートカットキーの設定(&K)")+"...",

	"HELP_UPDATE":_("最新バージョンを確認(&U)")+"...",
	"HELP_VERSIONINFO":_("バージョン情報(&V)")+"...",
}


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
	"FUNCTION_PLAY_POSE":_("停止"),
	"FUNCTION_VOLUME_UP":_("音量を上げる"),
	"FUNCTION_VOLUME_DOWN":_("音量を下げる"),
	"OPTION_OPTION":_("オプション(&O)")+"...",
	"OPTION_KEY_CONFIG":_("ショートカットキーの設定(&K)")+"...",

	"HELP_UPDATE":_("最新バージョンを確認(&U)")+"...",
	"HELP_VERSIONINFO":_("バージョン情報(&V)")+"...",
}

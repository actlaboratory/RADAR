
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
	"FILE_RELOAD":_("画面をリロード"),

	"FUNCTION_PLAY_PLAY":_("再生"),
	"FUNCTION_PLAY_MUTE":_("ミュート"),
	"FUNCTION_VOLUME_UP":_("音量を上げる"),
	"FUNCTION_VOLUME_DOWN":_("音量を下げる"),
	"FUNCTION_OUTPUT_CHANGEDEVICE":_("出力先デバイスの変更"),

	"SHOW_PROGRAMLIST":_("番組表の表示"),
	"HIDE_PROGRAMINFO":_("番組情報を非表示"),
	"UPDATE_PROGRAMLIST":_("放送局の一覧を再描画"),

	"RECORDING_IMMEDIATELY":_("今すぐ録音(&r)"),
	"RECORDING_SCHEDULE":_("予約録音(&s)"),
	"RECORDING_SCHEDULE_REMOVE":_("予約録音の取り消し"),
	"RECORDING_OPTION":_("録音品質の選択"),
	"RECORDING_MP3":_("MP3(&M)"),
	"RECORDING_WAV":_("WAV(&W)"),

	"OPTION_OPTION":_("設定(&O)")+"...",
	"OPTION_KEY_CONFIG":_("ショートカットキーの設定(&K)")+"...",

	"HELP_UPDATE":_("最新バージョンを確認(&U)")+"...",
	"HELP_VERSIONINFO":_("バージョン情報(&V)")+"...",
}


import re

def getValueString(ref_id):
	""" ナビキーとダイアログ文字列を消去した文字列を取り出し """
	dicVal = dic[ref_id]
	s = re.sub("\.\.\.$", "", dicVal)
	s = re.sub("\(&.\)$", "", s)
	return re.sub("&", "", s)

dic={
	"EXIT": _("終了(&X)"),
	"FILE_RELOAD":_("画面をリロード"),

	"FUNCTION_PLAY_PLAY":_("再生"),
	"FUNCTION_PLAY_MUTE":_("ミュート"),
	"FUNCTION_VOLUME_UP":_("音量を上げる"),
	"FUNCTION_VOLUME_DOWN":_("音量を下げる"),
	"FUNCTION_OUTPUT_CHANGEDEVICE":_("出力先デバイスの変更"),

	"SHOW_PROGRAMLIST":_("番組表の表示"),
	"HIDE_PROGRAMINFO":_("番組情報を非表示"),
	"UPDATE_PROGRAMLIST":_("放送局の一覧を再描画"),
	"PROGRAM_SEARCH":_("番組検索(&F)")+"...",

	"RECORDING_IMMEDIATELY":_("今すぐ録音(&r)"),
	"RECORDING_SCHEDULE":_("予約録音(&s)"),
	"RECORDING_SCHEDULE_MANAGE":_("スケジュール管理(&M)")+"...",
	"RECORDING_MANAGE":_("録音管理(&R)")+"...",
	"RECORDING_OPTION":_("録音品質の選択"),
	"RECORDING_MP3":_("MP3(&M)"),
	"RECORDING_WAV":_("WAV(&W)"),

	"OPTION_OPTION":_("設定(&O)")+"...",
	"OPTION_KEY_CONFIG":_("ショートカットキーの設定(&K)")+"...",
	"OPTION_STARTUP":_("Windows起動時の自動起動を有効化(&W)"),

	"HELP_UPDATE":_("最新バージョンを確認(&U)")+"...",
	"HELP_VERSIONINFO":_("バージョン情報(&V)")+"...",

	"SHOW":_("表示(&S)"),

}

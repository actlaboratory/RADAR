# RADAR

RADAR は、radiko.jp のライブ配信および聴き逃し（タイムフリー）配信の再生、録音、予約録音を行う Windows 向けデスクトップアプリケーションです。  
GUI は `wxPython`、再生は `mpv`、録音は `ffmpeg` を利用しています。

## 主な機能

- ライブ放送の再生（放送局ツリーから選択）
- ライブ放送の即時録音（`mp3` / `wav` / `m4a`）
- 予約録音（スケジュール管理）
- 聴き逃し再生/録音（番組表から実行）
- 聴き逃し再生の停止位置再開、シークバー移動
- 番組情報表示、番組検索、録音管理

## 技術構成

- 言語: Python 3
- GUI: `wxPython`
- 再生: `mpv.exe`（`views/mpvPlayer.py` 経由）
- 録音: `ffmpeg.exe`（`recorder.py`）
- API 通信: `requests`, `urllib`
- データ形式: XML（番組表・放送局情報）

## アーキテクチャ概要

- `views/main.py`  
  メインウィンドウ、メニュー、ショートカット、各ハンドラの統合。
- `views/radioManager.py`  
  ライブ/タイムフリー再生状態管理、再生停止、UI状態同期。
- `views/programmanager.py`  
  認証セッション管理、放送局/番組取得、タイムフリーURL生成。
- `views/recordingWizzard.py`  
  番組表ダイアログ、聴き逃し再生/録音、シークUI。
- `recorder.py`  
  録音処理本体（プロセス起動、監視、停止、エラーハンドリング）。

## タイムフリー実装方針

- 認証は `auth1 -> partialKey -> auth2` の2段階で `AuthToken` を取得。
- 再生URLは `ts/playlist.m3u8` 優先、失敗時は `playlist_create_url` にフォールバック。
- シークは `mpv` のローカルシークではなく、`seek` パラメータ付きURLを再生成して再接続。
- ライブ再生（`F1`）と聴き逃し再生（`F2`）は独立した操作系で管理。

## エリアフリー（radikoプレミアム）

- 「オプション → radikoプレミアムにログイン」から、エリアフリープランを契約した会員アカウントでログインすると、全国の放送局を地方別のツリーで表示・再生/録音できる。
- ログイン（`v4/api/member/login`）の応答の `areafree` が `1` のときだけ有効化し、auth2 に `radiko_session` を付与してトークンを取得する（`views/premium.py`）。
- エリアフリー時は、局ごとの stream XML から `areafree="1"` の playlist URL を使い、playlist の `type` を `c` にする（`a`/`b` はエリア内専用で、エリア外の局は 403 になる）。
- 認証情報を保存する場合、パスワードは DPAPI で暗号化して設定ファイルに保存する。
- エリアフリー時は番組検索用キャッシュも全国の局を収集する。キャッシュがどの範囲（自エリア/全国）で作られたかを `cache_metadata` の `station_scope` に記録し、ログイン状態と一致しなければ起動時・ログイン/ログアウト時に再収集する。

## 録音形式

- `mp3`: エンコード保存
- `wav`: PCM保存
- `m4a`: AACストリームをコピー保存（`aac_adtstoasc`）

## 実行環境

- OS: Windows 64-bit
- Python: 32bit版 Python の利用を推奨
- 同梱または配置が必要な実行ファイル:
  - `bin/mpv.exe`
  - `ffmpeg.exe`（必要DLL含む）

## 起動

- 起動用ファイルは `radar.py`
- 例: `python radar.py`

## 注意事項

- radiko 側仕様変更により、再生/録音/認証フローが影響を受ける場合があります。
- エリア制限により、環境によって聴取可能局が異なります。
- 録音データは法令と利用規約の範囲で利用してください。
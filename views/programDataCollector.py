# -*- coding: utf-8 -*-
# 番組データ収集モジュール

import threading
import datetime
import time
from logging import getLogger
import lxml.etree as ET
import requests
import constants
from views import programmanager
from views import radioManager
from views.programCacheManager import ProgramCacheManager

# 1放送局分の約2週間（過去7日〜6日先）を1リクエストで返す
WEEKLY_PROGRAM_URL = programmanager.RADIKO_API_BASE + "/program/station/weekly/{station_id}.xml"
DATE_PROGRAM_URL = programmanager.RADIKO_API_BASE + "/program/station/date/{date}/{station_id}.xml"

# radiko への負荷を抑えるため、リクエストの間隔を最低これだけ空ける
REQUEST_INTERVAL_SEC = 1.0
REQUEST_TIMEOUT_SEC = 15
# 1リクエストあたりの再試行回数と、再試行前の待機（指数的に伸ばす）
MAX_RETRIES = 2
RETRY_BASE_DELAY_SEC = 5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
# この数の放送局で続けて失敗したら、radiko 側の不調とみなして収集を打ち切る
MAX_CONSECUTIVE_FAILURES = 3
# 打ち切った後、この時間は新たな収集を始めない
ABORT_COOLDOWN_SEC = 600

# 放送局ごとの収集済みマーカー（値は収集時のラジオ基準日）。中断後の再開時に取得済みの局を飛ばす
COLLECTED_KEY_PREFIX = "collected:"

# 収集を打ち切った後の待機期限（time.monotonic 基準）。収集器のインスタンスをまたいで共有する
_cooldown_lock = threading.Lock()
_cooldown_until = 0.0


class RequestFailed(Exception):
    """再試行しても radiko から応答を得られなかった"""


class CollectionAborted(Exception):
    """収集の停止が要求された"""


class ProgramDataCollector:
    """全放送局の番組データを収集・管理するクラス"""

    def __init__(self, cache_manager=None):
        self.log = getLogger(f"{constants.LOG_PREFIX}.ProgramDataCollector")
        self.cache_manager = cache_manager or ProgramCacheManager()
        self.radio_manager = None  # 後で設定
        self.collection_thread = None
        self.is_collecting = False
        self.collection_interval = 3600  # 1時間ごと
        self.progress_callback = None
        self._stop_event = threading.Event()
        self._last_request_at = 0.0
        # 1回の収集で送ったリクエスト数と開始時刻（ログ用。再試行も1回と数える）
        self._request_count = 0
        self._collection_started_at = 0.0

    def set_radio_manager(self, radio_manager):
        """RadioManagerを設定"""
        self.radio_manager = radio_manager

    def set_progress_callback(self, callback):
        """進捗通知コールバック (current, total, status_text) を設定"""
        self.progress_callback = callback

    def _report_progress(self, current, total, status_text=None):
        if self.progress_callback:
            try:
                self.progress_callback(current, total, status_text)
            except Exception as e:
                self.log.warning(f"Progress callback failed: {e}")

    def _get_station_name(self, station_id):
        if self.radio_manager and hasattr(self.radio_manager, 'stid'):
            return self.radio_manager.stid.get(station_id, station_id)
        return station_id

    def _reset_request_stats(self):
        self._request_count = 0
        self._collection_started_at = time.monotonic()

    def _request_stats(self):
        elapsed = time.monotonic() - self._collection_started_at
        return f"{self._request_count} requests in {elapsed:.1f}s"

    def _in_cooldown(self):
        with _cooldown_lock:
            remaining = _cooldown_until - time.monotonic()
        if remaining > 0:
            self.log.warning(f"Program collection was aborted recently; skipping for another {remaining:.0f}s")
            return True
        return False

    def _start_cooldown(self):
        global _cooldown_until
        with _cooldown_lock:
            _cooldown_until = time.monotonic() + ABORT_COOLDOWN_SEC
        self.log.warning(
            f"{MAX_CONSECUTIVE_FAILURES} stations failed in a row; aborting collection "
            f"and pausing for {ABORT_COOLDOWN_SEC}s ({self._request_stats()})"
        )

    def _wait(self, seconds):
        """停止要求があれば CollectionAborted を送出する待機"""
        if self._stop_event.wait(max(0.0, seconds)):
            raise CollectionAborted()

    def _fetch_xml(self, url):
        """radiko から XML を取得する。

        リトライしても意味のない応答（404 など・XML 不正）は None を返し、
        タイムアウトや 5xx が再試行後も続く場合は RequestFailed を送出する。
        """
        reason = ""
        for attempt in range(MAX_RETRIES + 1):
            self._wait(self._last_request_at + REQUEST_INTERVAL_SEC - time.monotonic())
            retry_after = 0
            self._request_count += 1
            try:
                response = requests.get(url, headers={"User-Agent": constants.BROWSER_USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            except requests.RequestException as e:
                reason = str(e)
            else:
                if response.status_code == 200:
                    try:
                        return ET.fromstring(response.content)
                    except ET.ParseError as e:
                        self.log.warning(f"Failed to parse XML from {url}: {e}")
                        return None
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    self.log.warning(f"HTTP {response.status_code} from {url}; skipping")
                    return None
                reason = f"HTTP {response.status_code}"
                retry_after_header = response.headers.get("Retry-After", "")
                if retry_after_header.isdigit():
                    retry_after = int(retry_after_header)
            finally:
                self._last_request_at = time.monotonic()

            if attempt < MAX_RETRIES:
                delay = max(RETRY_BASE_DELAY_SEC * (2 ** attempt), retry_after)
                self.log.info(f"Request to {url} failed ({reason}); retrying in {delay}s")
                self._wait(delay)
        raise RequestFailed(f"{url}: {reason}")

    def _parse_programs_by_date(self, root):
        """番組表 XML を {日付: [番組]} に変換する"""
        programs_by_date = {}
        for progs in root.iter("progs"):
            date_str = (progs.findtext("date") or "").strip()
            if not date_str:
                continue
            programs = programs_by_date.setdefault(date_str, [])
            for prog in progs.findall("prog"):
                title = (prog.findtext("title") or "").strip()
                if not title:
                    continue
                programs.append({
                    'title': title,
                    'performer': (prog.findtext("pfm") or "").strip(),
                    'start_time': self._format_time(prog.get("ftl") or ""),
                    'end_time': self._format_time(prog.get("tol") or ""),
                    'description': (prog.findtext("desc") or "").strip(),
                })
        return programs_by_date

    def _collect_station_date(self, station_id, date):
        """単一放送局・単一日付の番組を取得する。取得できなければ None"""
        root = self._fetch_xml(DATE_PROGRAM_URL.format(date=date, station_id=station_id))
        if root is None:
            return None
        return self._parse_programs_by_date(root).get(date, [])

    def _collect_station_weekly(self, station_id, date_list):
        """単一放送局の date_list 分の番組を取得する。

        戻り値は (programs_by_date, complete)。週間APIで足りない日付だけ日付APIで補う。
        complete は通信エラーで取れなかった日付があれば False（404 などで番組表が無い日は取り直さない）。
        週間APIの取得自体が再試行後も失敗した場合は RequestFailed を送出する。
        """
        root = self._fetch_xml(WEEKLY_PROGRAM_URL.format(station_id=station_id))
        programs_by_date = {}
        if root is None:
            # 週間APIが使えない局は、従来どおり日付ごとに取得する
            self.log.warning(f"Weekly listings unavailable for {station_id}; falling back to per-date requests")
        else:
            wanted = set(date_list)
            programs_by_date = {
                date_str: programs
                for date_str, programs in self._parse_programs_by_date(root).items()
                if date_str in wanted
            }
        complete = True
        for date_str in date_list:
            if date_str in programs_by_date:
                continue
            try:
                programs = self._collect_station_date(station_id, date_str)
            except RequestFailed as e:
                self.log.warning(f"Failed to collect {station_id} on {date_str}: {e}")
                complete = False
                break
            if programs is None:
                continue
            programs_by_date[date_str] = programs
        return programs_by_date, complete

    def collect_all_stations_data(self, date=None, force_refresh=False):
        """全放送局の番組データを収集"""
        if not self.radio_manager:
            self.log.error("RadioManager not set")
            return False

        if date is None:
            # ラジオの日付ルールに従った日付を取得
            from tcutil import CalendarUtil
            calendar_util = CalendarUtil()
            date = calendar_util.get_radio_date()

        # キャッシュの有効性をチェック
        if not force_refresh and self.cache_manager.is_cache_valid(date):
            self.log.info(f"Cache is valid for date {date}, skipping collection")
            return True

        if self._in_cooldown():
            return False

        self.log.info(f"Starting data collection for date {date}")
        self._reset_request_stats()

        try:
            # 全放送局のIDを取得
            if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
                self.log.error("RadioManager or station list not available")
                return False

            station_ids = list(self.radio_manager.stid.keys())
            self.log.info(f"Found {len(station_ids)} stations to collect: {station_ids[:5]}...")  # first 5

            success_count = 0
            consecutive_failures = 0
            total = len(station_ids)

            for index, station_id in enumerate(station_ids, start=1):
                station_name = self._get_station_name(station_id)
                self._report_progress(
                    index,
                    total,
                    _("%(date)s %(station)s を取得中") % {"date": date, "station": station_name},
                )
                try:
                    programs = self._collect_station_date(station_id, date)
                except RequestFailed as e:
                    self.log.warning(f"Failed to collect data for station {station_id}: {e}")
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        self._start_cooldown()
                        return False
                    continue
                consecutive_failures = 0
                if programs is None:
                    continue
                self.cache_manager.replace_station_programs(station_id, station_name, {date: programs})
                success_count += 1

            self.log.info(
                f"Collected data for {success_count}/{len(station_ids)} stations on {date} ({self._request_stats()})"
            )
            return success_count > 0

        except CollectionAborted:
            self.log.info(f"Data collection stopped ({self._request_stats()})")
            return False
        except Exception as e:
            self.log.error(f"Data collection failed: {e}")
            return False

    def collect_weekly_data(self, start_date=None, force_refresh=False):
        """ラジオ基準日の前後1週間分のデータを放送局ごとにまとめて収集する。

        force_refresh が False なら、同じ基準日で収集済みの放送局は取得しない（中断後の再開用）。
        """
        if not self.radio_manager:
            self.log.error("RadioManager not set")
            return False

        if start_date is None:
            from tcutil import CalendarUtil
            base_date = CalendarUtil().get_radio_base_datetime()
        elif isinstance(start_date, str):
            base_date = datetime.datetime.strptime(start_date, '%Y%m%d')
        else:
            base_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        base_date_str = base_date.strftime('%Y%m%d')

        # ラジオ基準日から過去7日 + 基準日から7日先まで（計15日）。
        # 番組表・番組検索の日付選択肢（過去1週 + 番組表8日分）と一致させる。
        first_day = base_date - datetime.timedelta(days=7)
        date_list = []
        for i in range(15):
            target_date = first_day + datetime.timedelta(days=i)
            date_list.append(target_date.strftime('%Y%m%d'))

        # 全放送局のIDを取得
        if not self.radio_manager or not hasattr(self.radio_manager, 'stid') or not self.radio_manager.stid:
            self.log.error("RadioManager or station list not available")
            return False

        if self._in_cooldown():
            return False

        station_ids = list(self.radio_manager.stid.keys())
        self.log.info(
            f"Starting program cache collection from {date_list[0]} to {date_list[-1]} "
            f"for {len(station_ids)} stations (force_refresh={force_refresh})"
        )
        self._reset_request_stats()

        success_count = 0
        skipped_count = 0
        failed_count = 0
        consecutive_failures = 0
        total = len(station_ids)

        try:
            for index, station_id in enumerate(station_ids, start=1):
                station_name = self._get_station_name(station_id)
                collected_key = COLLECTED_KEY_PREFIX + station_id
                if not force_refresh and self.cache_manager.get_metadata(collected_key) == base_date_str:
                    skipped_count += 1
                    continue
                self._report_progress(
                    index,
                    total,
                    _("%(station)s の番組表を取得中") % {"station": station_name},
                )
                try:
                    programs_by_date, complete = self._collect_station_weekly(station_id, date_list)
                except RequestFailed as e:
                    self.log.warning(f"Failed to collect weekly data for station {station_id}: {e}")
                    failed_count += 1
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        self._start_cooldown()
                        return False
                    continue
                consecutive_failures = 0

                if programs_by_date:
                    self.cache_manager.replace_station_programs(station_id, station_name, programs_by_date)
                if complete:
                    self.cache_manager.set_metadata(collected_key, base_date_str)
                    success_count += 1
                else:
                    failed_count += 1
        except CollectionAborted:
            self.log.info(f"Weekly data collection stopped ({self._request_stats()})")
            return False

        self.log.info(
            f"Weekly data collection finished: {success_count} collected, "
            f"{skipped_count} already up to date, {failed_count} failed ({self._request_stats()})"
        )
        # ログアウト等で一覧から外れた局の番組は、検索に出ないよう失敗の有無にかかわらず消す
        self.cache_manager.delete_programs_except_stations(station_ids)
        if failed_count == 0:
            # 全局そろったときだけ更新済みとする（失敗した局は次回起動時に取り直す）
            self.cache_manager.set_metadata('last_update', base_date_str)
        if success_count + skipped_count > 0:
            return True
        self.log.error("Weekly data collection failed for all stations")
        return False

    def _format_time(self, time_str):
        """時間文字列をフォーマット"""
        if not time_str or len(time_str) < 4:
            return ''

        # HHMM形式をHH:MM:SS形式に変換
        if len(time_str) == 4:
            return f"{time_str[:2]}:{time_str[2:4]}:00"
        return time_str

    def start_background_collection(self):
        """バックグラウンドでの定期データ収集を開始"""
        if self.is_collecting:
            self.log.warning("Background collection already running")
            return

        self.is_collecting = True
        self.collection_thread = threading.Thread(target=self._background_collection_loop, daemon=True)
        self.collection_thread.start()
        self.log.info("Background collection started")

    def stop_background_collection(self):
        """バックグラウンド収集を停止"""
        self.is_collecting = False
        if self.collection_thread and self.collection_thread.is_alive():
            self.collection_thread.join(timeout=0.2)
        self.collection_thread = None
        self.log.info("Background collection stopped")

    def _background_collection_loop(self):
        """バックグラウンド収集ループ"""
        while self.is_collecting:
            try:
                # ラジオの日付ルールに従った日付を取得
                from tcutil import CalendarUtil
                calendar_util = CalendarUtil()
                today = calendar_util.get_radio_date()
                tomorrow = (datetime.datetime.strptime(today, '%Y%m%d') + datetime.timedelta(days=1)).strftime('%Y%m%d')

                self.log.debug(f"Collecting data for today: {today}, tomorrow: {tomorrow}")

                self.collect_all_stations_data(today)
                self.collect_all_stations_data(tomorrow)

                # 古いデータをクリーンアップ
                self.cache_manager.cleanup_old_data(days=21)

                # 次の収集まで待機
                if self._stop_event.wait(self.collection_interval):
                    break

            except Exception as e:
                self.log.error(f"Background collection error: {e}")
                if self._stop_event.wait(60):  # エラー時は1分待機
                    break

    def get_available_dates(self):
        """利用可能な日付のリストを取得"""
        try:
            cursor = self.cache_manager.conn.cursor()
            cursor.execute("SELECT DISTINCT date FROM programs ORDER BY date DESC")
            dates = [row[0] for row in cursor.fetchall()]
            return dates
        except Exception as e:
            self.log.error(f"Failed to get available dates: {e}")
            return []

    def get_station_list(self):
        """収集済みの放送局リストを取得"""
        try:
            cursor = self.cache_manager.conn.cursor()
            cursor.execute("SELECT DISTINCT station_id, station_name FROM programs ORDER BY station_name")
            stations = [(row[0], row[1]) for row in cursor.fetchall()]
            return stations
        except Exception as e:
            self.log.error(f"Failed to get station list: {e}")
            return []

    def cleanup(self):
        """リソースのクリーンアップ"""
        self.log.info("ProgramDataCollector.cleanup: stopping background collection")
        self._stop_event.set()
        self.stop_background_collection()
        self.log.info("ProgramDataCollector.cleanup: done")

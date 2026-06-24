#programmanager

import hashlib
import random
import re
import lxml.etree as ET
from logging import getLogger
import requests
import constants
import datetime
import urllib.parse
from views import token

LIVE_PLAYLIST_URL = "https://alliance-stream-radiko.smartstream.ne.jp/so/playlist.m3u8"
TIMEFREE_PLAYLIST_URL = "https://tf-rpaa.smartstream.ne.jp/tf/playlist.m3u8"
RADIKO_API_BASE = "https://radiko.jp/v3"

class ProgramManager:
    def __init__(self):
        self.log=getLogger("%s.%s" % (constants.LOG_PREFIX,"ProgramManager"))
        self.log.debug("created!")
        self.gettoken = None
        self.token = None
        self.partialkey = None
        self.area_id = ""
        self.jpCode()

    def refresh_auth_session(self):
        """radiko認証セッションを毎回更新する"""
        self.gettoken = token.Token()
        res = self.gettoken.auth1()
        ret = self.gettoken.get_partial_key(res)
        self.token = ret[1]
        self.partialkey = ret[0]
        auth2_text = self.gettoken.auth2(self.partialkey, self.token)
        self.area_id = self._extract_area_id(auth2_text)
        return self.token

    def getArea(self):
        """エリアを判定する"""
        self.refresh_auth_session()
        if not self.area_id:
            raise RuntimeError("Failed to get area from auth2 response")
        return self.area_id

    @staticmethod
    def _make_lsid():
        return hashlib.md5(str(random.random()).encode("utf-8")).hexdigest()

    def _live_auth_headers(self):
        return {"X-Radiko-AuthToken": self.token}

    def get_live_playback_source(self, station_id):
        """認証済みライブ再生URLとHTTPヘッダーを返す"""
        self.refresh_auth_session()
        params = urllib.parse.urlencode({
            "station_id": station_id,
            "l": 15,
            "lsid": self._make_lsid(),
            "type": "b",
        })
        url = f"{LIVE_PLAYLIST_URL}?{params}"
        return url, self._live_auth_headers()

    def get_authenticated_stream_url(self, station_id):
        """認証済みライブ再生URLとHTTPヘッダーを返す"""
        return self.get_live_playback_source(station_id)

    def get_timefree_stream_url(self, station_id, ft_dt, to_dt):
        """互換用: タイムフリー再生URLと同等のURLを返す"""
        stream_url, _headers = self.get_timefree_playback_source(station_id, ft_dt, to_dt)
        return stream_url

    def get_timefree_playback_source(self, station_id, ft_dt, to_dt):
        """タイムフリー再生用 URL と必須ヘッダーを返す"""
        self.refresh_auth_session()
        url = self._build_timefree_playlist_url(station_id, ft_dt, to_dt, stream_type="b")
        return url, self._live_auth_headers()

    def get_timefree_playback_source_compat(self, station_id, ft_dt, to_dt):
        """タイムフリー再生互換URL(type=c)"""
        self.refresh_auth_session()
        url = self._build_timefree_playlist_url(station_id, ft_dt, to_dt, stream_type="c")
        return url, self._live_auth_headers()

    def get_timefree_playback_source_with_seek(self, station_id, ft_dt, to_dt, seek_seconds=0, stream_type="b"):
        """seek位置を指定してタイムフリー再生URLとヘッダーを返す"""
        self.refresh_auth_session()
        if seek_seconds is None:
            seek_seconds = 0
        seek_seconds = max(0, int(seek_seconds))
        total_duration = max(1, int((to_dt - ft_dt).total_seconds()))
        seek_seconds = min(seek_seconds, total_duration - 1)
        seek_dt = ft_dt + datetime.timedelta(seconds=seek_seconds)
        url = self._build_timefree_playlist_url(
            station_id,
            ft_dt,
            to_dt,
            seek_dt=seek_dt,
            stream_type=stream_type,
        )
        return url, self._live_auth_headers()

    def get_timefree_recording_segments(self, station_id, ft_dt, to_dt):
        """タイムフリー録音用セグメント (url, headers, duration_sec) のリスト。

        radiko 側の制約で l は最大 300 秒のため、長時間番組は複数セグメントに分割する。
        """
        self.refresh_auth_session()
        total_sec = int(max(1, (to_dt - ft_dt).total_seconds()))
        if total_sec <= 300:
            url = self._build_timefree_playlist_url(
                station_id, ft_dt, to_dt, stream_type="c", l_value=total_sec
            )
            return [(url, self._live_auth_headers(), total_sec)]
        return self._build_timefree_playlist_recording_segments(station_id, ft_dt, to_dt)

    def get_timefree_recording_source(self, station_id, ft_dt, to_dt):
        """タイムフリー録音用 URL（単一セグメント時のみ）。長時間は get_timefree_recording_segments を使う。"""
        segments = self.get_timefree_recording_segments(station_id, ft_dt, to_dt)
        if len(segments) != 1:
            raise RuntimeError(
                "この番組はタイムフリー録音に複数セグメントが必要です。"
            )
        return segments[0][0], segments[0][1]

    @staticmethod
    def _radiko_timefree_chunk_len_seconds(remain_sec: int) -> int:
        """rec_radiko_ts 準拠: 残りが 300 秒未満のとき 5 秒境界へ切り上げ。"""
        if remain_sec <= 0:
            return 0
        if remain_sec >= 300:
            return 300
        if remain_sec % 5 == 0:
            return remain_sec
        return ((remain_sec // 5) + 1) * 5

    def _build_timefree_playlist_url(
        self,
        station_id,
        ft_dt,
        to_dt,
        seek_dt=None,
        stream_type="b",
        l_value=15,
        lsid=None,
    ):
        """新タイムフリーAPI向け playlist URL を構築する"""
        ft = self._format_radiko_time(ft_dt)
        to = self._format_radiko_time(to_dt)
        seek = self._format_radiko_time(seek_dt or ft_dt)
        query = urllib.parse.urlencode({
            "station_id": station_id,
            "start_at": ft,
            "ft": ft,
            "seek": seek,
            "end_at": to,
            "to": to,
            "l": str(int(l_value)),
            "lsid": lsid or self._make_lsid(),
            "type": stream_type,
        })
        return f"{TIMEFREE_PLAYLIST_URL}?{query}"

    def _build_timefree_playlist_recording_segments(self, station_id, ft_dt, to_dt):
        """新タイムフリーAPIを l<=300 秒で繰り返し、セグメント一覧を返す。"""
        lsid = self._make_lsid()
        headers = self._live_auth_headers()
        segments = []
        cursor = ft_dt
        while cursor < to_dt:
            remain = int((to_dt - cursor).total_seconds())
            if remain <= 0:
                break
            chunk_nominal = self._radiko_timefree_chunk_len_seconds(remain)
            tentative_end = cursor + datetime.timedelta(seconds=chunk_nominal)
            chunk_end = min(tentative_end, to_dt)
            l_eff = max(1, int((chunk_end - cursor).total_seconds()))
            url = self._build_timefree_playlist_url(
                station_id,
                ft_dt,
                chunk_end,
                seek_dt=cursor,
                stream_type="c",
                l_value=l_eff,
                lsid=lsid,
            )
            segments.append((url, dict(headers), l_eff))
            cursor = chunk_end
        if not segments:
            raise RuntimeError("timefree recording segments empty")
        return segments

    def _format_radiko_time(self, dt_obj):
        if not isinstance(dt_obj, datetime.datetime):
            raise TypeError("dt_obj must be datetime")
        return dt_obj.strftime("%Y%m%d%H%M%S")

    def _extract_area_id(self, auth2_text):
        """auth2レスポンスから JPxx を抽出"""
        text = (auth2_text or "").strip()
        if not text:
            return ""
        first_line = text.splitlines()[0]
        first_token = first_line.split(",")[0].strip()
        match = re.search(r"(JP\d{1,2})", first_token)
        if match:
            return match.group(1)
        match = re.search(r"(JP\d{1,2})", text)
        return match.group(1) if match else ""

    def getprogramlist(self):
        return RADIKO_API_BASE

    def retrieveRadioListings(self, id, date):
        try:
            # 日付の処理を修正
            if isinstance(date, str):
                if len(date) == 8 and date.isdigit():  # YYYYMMDD形式
                    formatted_date = date
                elif ',' in date:  # カンマ区切り形式
                    lists = date.split(",")
                    if len(lists) >= 3:
                        year = lists[0].strip()
                        month = lists[1].strip().zfill(2)
                        day = lists[2].strip().zfill(2)
                        formatted_date = f"{year}{month}{day}"
                        self.log.debug(f"Converted comma-separated date '{date}' to '{formatted_date}'")
                    else:
                        self.log.error(f"Invalid date format: {date}")
                        self.root = None
                        return
                elif '/' in date:  # スラッシュ区切り形式
                    lists = date.split("/")
                    if len(lists) >= 3:
                        year = lists[0].strip()
                        month = lists[1].strip().zfill(2)
                        day = lists[2].strip().zfill(2)
                        formatted_date = f"{year}{month}{day}"
                        self.log.debug(f"Converted slash-separated date '{date}' to '{formatted_date}'")
                    else:
                        self.log.error(f"Invalid date format: {date}")
                        self.root = None
                        return
                elif '-' in date:  # ハイフン区切り形式
                    lists = date.split("-")
                    if len(lists) >= 3:
                        year = lists[0].strip()
                        month = lists[1].strip().zfill(2)
                        day = lists[2].strip().zfill(2)
                        formatted_date = f"{year}{month}{day}"
                        self.log.debug(f"Converted hyphen-separated date '{date}' to '{formatted_date}'")
                    else:
                        self.log.error(f"Invalid date format: {date}")
                        self.root = None
                        return
                else:
                    self.log.error(f"Unsupported date format: {date}")
                    self.root = None
                    return
            else:
                self.log.error(f"Date must be string, got: {type(date)}")
                self.root = None
                return
            
            url = f"{self.getprogramlist()}/program/station/date/{formatted_date}/{id}.xml"
            self.log.debug(f"Requesting URL: {url}")

            # XMLデータを取得
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            xml_data = response.content
            
            # XMLを解析
            self.root = ET.fromstring(xml_data)
            self.log.debug(f"Successfully retrieved listings for station {id} on {formatted_date}")
            
        except requests.RequestException as e:
            self.log.error(f"Failed to retrieve radio listings for station {id}: {e}")
            self.root = None
        except ET.ParseError as e:
            self.log.error(f"Failed to parse XML for station {id}: {e}")
            self.root = None
        except Exception as e:
            self.log.error(f"Unexpected error retrieving listings for station {id}: {e}")
            import traceback
            self.log.error(f"Traceback: {traceback.format_exc()}")
            self.root = None

    def gettitle(self):
        try:
            if not hasattr(self, 'root') or self.root is None:
                self.log.warning("Root element not available")
                return []
            title_elements = self.root.findall(".//title")
            titles = [title.text if title.text else '' for title in title_elements]
            return titles
        except Exception as e:
            self.log.error(f"Failed to get titles: {e}")
            return []

    def getpfm(self):
        try:
            if not hasattr(self, 'root') or self.root is None:
                self.log.warning("Root element not available")
                return []
            pfm_elements = self.root.findall(".//pfm")
            names = [pfm.text if pfm.text else '' for pfm in pfm_elements]
            return names
        except Exception as e:
            self.log.error(f"Failed to get performers: {e}")
            return []

    def jpCode(self):
        """stationIdをキー、都道府県コードを値に持つ辞書を作成"""
        self.values = {}
        url = f"{self.getprogramlist()}/station/region/full.xml"
        response = requests.get(url)
        xml_data = response.content
        root = ET.fromstring(xml_data)
        id_elements = root.findall(".//id")
        area_id_elements = root.findall(".//area_id")
        station_id = [id.text for id in id_elements]
        area_id = [areaid.text for areaid in area_id_elements]
        for station,area in zip(station_id, area_id):
            self.values[station] = area

    def getNowProgram(self, id):
        """現在再生中の番組タイトルを返す（エリア単位APIを使用）"""
        try:
            return self._getNowProgramByArea(id)
        except Exception as e:
            self.log.error(f"Unexpected error in getNowProgram: {e}")
            return None

    def _getNowProgramByArea(self, id):
        """都道府県コードを使用して番組情報を取得"""
        try:
            title_dic = {} #stationidをキー、番組名を値とする辞書
            if id not in self.values:
                self.log.warning(f"Station ID {id} not found in values")
                return None
                
            jp_number = self.values[id]
            url = f"{self.getprogramlist()}/program/now/{jp_number}.xml"
            self.log.debug(f"Fetching now program for {id} (area: {jp_number}): {url}")
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
            except requests.RequestException as e:
                self.log.error(f"Failed to fetch program data by area: {e}")
                return None
                
            try:
                root = ET.fromstring(response.content)
                results = root.xpath(".//station")
                progs = root.xpath(".//progs")
            except Exception as e:
                self.log.error(f"Failed to parse XML by area: {e}")
                return None
                
            self.url = url
            self.progs = progs
            self.results = results
            self.response = response
            
            for result, title in zip(results, progs):
                try:
                    title_element = title.xpath(".//title")
                    if title_element and title_element[0].text:
                        title_dic[result.get("id")] = title_element[0].text
                except Exception as e:
                    self.log.warning(f"Failed to extract title for station {result.get('id')}: {e}")
                    continue

            #stationidに該当する番組名を返す
            if id in title_dic:
                self.log.debug(f"Found program title via area-based API for {id}: {title_dic[id]}")
                return title_dic[id]
            else:
                self.log.warning(f"No program found for station ID {id} in area {jp_number}")
                return None
                
        except Exception as e:
            self.log.error(f"Unexpected error in _getNowProgramByArea: {e}")
            return None

    def getnowProgramPfm(self, id):
        """現在放送中の番組の出演者を返す"""
        try:
            # 直接取得した場合、該当する放送局の出演者情報を探す
            for result, prog in zip(self.results, self.progs):
                if result.get("id") == id:
                    try:
                        pfm_element = prog.xpath(".//pfm")
                        if pfm_element and pfm_element[0].text:
                            return pfm_element[0].text
                    except Exception as e:
                        self.log.warning(f"Failed to extract performer for station {id}: {e}")
                        continue
            
            # 見つからない場合は別の方法で検索
            pfm_dic = {}
            for result, pfm in zip(self.results, self.progs):
                try:
                    pfm_element = pfm.xpath(".//pfm")
                    if pfm_element and pfm_element[0].text:
                        pfm_dic[result.get("id")] = pfm_element[0].text
                except Exception as e:
                    self.log.warning(f"Failed to extract performer for station {result.get('id')}: {e}")
                    continue

            if id in pfm_dic:
                return pfm_dic[id]
            else:
                return ""
        except Exception as e:
            self.log.error(f"Unexpected error in getnowProgramPfm: {e}")
            return None

    def getNowProgramDsc(self, id):
        """番組の説明を取得して返す"""
        try:
            # 直接取得した場合、該当する放送局の説明情報を探す
            for result, prog in zip(self.results, self.progs):
                if result.get("id") == id:
                    try:
                        desc_element = prog.xpath(".//desc")
                        if desc_element and desc_element[0].text:
                            desc_text = desc_element[0].text
                            # HTMLタグを除去
                            clean_text = re.sub(re.compile('<.*?>'), '', desc_text)
                            return clean_text
                    except Exception as e:
                        self.log.warning(f"Failed to extract description for station {id}: {e}")
                        continue
            
            # 見つからない場合は従来の方法で検索
            dsc_dic = {}
            for result, dsc in zip(self.results, self.progs):
                try:
                    desc_element = dsc.xpath(".//desc")
                    if desc_element and desc_element[0].text:
                        desc_text = desc_element[0].text
                        # HTMLタグを除去
                        clean_text = re.sub(re.compile('<.*?>'), '', desc_text)
                        dsc_dic[result.get("id")] = clean_text
                except Exception as e:
                    self.log.warning(f"Failed to extract description for station {result.get('id')}: {e}")
                    continue
                    
            if id in dsc_dic:
                return dsc_dic[id]
            else:
                return None
        except Exception as e:
            self.log.error(f"Unexpected error in getNowProgramDsc: {e}")
            return None

    def get_ftl(self):
        try:
            if not hasattr(self, 'root') or self.root is None:
                self.log.warning("Root element not available")
                return []
            prog_elements = self.root.findall(".//prog")
            prog_ftl = [ftl.get("ftl") if ftl.get("ftl") else '' for ftl in prog_elements]
            return prog_ftl
        except Exception as e:
            self.log.error(f"Failed to get start times: {e}")
            return []

    def get_tol(self):
        try:
            if not hasattr(self, 'root') or self.root is None:
                self.log.warning("Root element not available")
                return []
            prog_elements = self.root.findall(".//prog")
            prog_tol = [tol.get("tol") if tol.get("tol") else '' for tol in prog_elements]
            return prog_tol
        except Exception as e:
            self.log.error(f"Failed to get end times: {e}")
            return []

    def get_onair_music(self, id):
        """オンエア中の曲情報を取得"""
        url = f'{self.getprogramlist()}/feed/pc/noa/{id}.xml'
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = root.xpath(".//item")
        return self._select_onair_music_from_items(items)

    def get_onair_music_at(self, station_id, target_dt=None):
        """オンエア曲を取得。target_dt が与えられた場合は最も近い過去の曲を返す。"""
        url = f'{self.getprogramlist()}/feed/pc/noa/{station_id}.xml'
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = root.xpath(".//item")
        return self._select_onair_music_from_items(items, target_dt=target_dt)

    def _select_onair_music_from_items(self, items, target_dt=None):
        if not items:
            return ""
        if target_dt is None:
            return self._format_onair_item(items[0])

        best = None
        best_dt = None
        for item in items:
            item_dt = self._parse_onair_item_datetime(item)
            if item_dt is None:
                continue
            if item_dt <= target_dt and (best_dt is None or item_dt > best_dt):
                best = item
                best_dt = item_dt
        if best is not None:
            return self._format_onair_item(best)

        return self._format_onair_item(items[0])

    def _format_onair_item(self, item):
        title = item.get("title", "")
        artist = item.get("artist", "")
        if title and artist:
            return f"{artist} - {title}"
        if title:
            return title
        return ""

    def _parse_onair_item_datetime(self, item):
        """noa feedの時刻属性を可能な範囲で解釈する。"""
        candidates = [
            item.get("time"),
            item.get("date"),
            item.get("onair_date"),
            item.get("timestamp"),
            item.get("start_time"),
        ]
        for value in candidates:
            dt_obj = self._parse_datetime_flex(value)
            if dt_obj is not None:
                return dt_obj
        return None

    def _parse_datetime_flex(self, value):
        if not value:
            return None
        text = str(value).strip()
        formats = (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M",
        )
        for fmt in formats:
            try:
                dt_obj = datetime.datetime.strptime(text, fmt)
                if dt_obj.tzinfo is not None:
                    dt_obj = dt_obj.astimezone().replace(tzinfo=None)
                return dt_obj
            except Exception:
                continue
        return None

    def getDescriptions(self):
        try:
            if not hasattr(self, 'root') or self.root is None:
                self.log.warning("Root element not available")
                return []
            desc_elements = self.root.findall(".//desc")
            descriptions = [description.text if description.text else '' for description in desc_elements]
            return descriptions
        except Exception as e:
            self.log.error(f"Failed to get descriptions: {e}")
            return []
#programmanager

import re
import lxml.etree as ET
from logging import getLogger
import requests
import constants
import datetime
import urllib.parse
import secrets
import tcutil
from views import token

class ProgramManager:
    def __init__(self):
        self.log=getLogger("%s.%s" % (constants.LOG_PREFIX,"ProgramManager"))
        self.log.debug("created!")
        self.gettoken = None
        self.token = None
        self.partialkey = None
        self.area_id = ""
        self.jpCode()
        self.tcutil = tcutil.CalendarUtil()

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

    def get_authenticated_stream_url(self, station_id):
        """認証済みの一時m3u8 URLを取得する"""
        self.refresh_auth_session()
        url = f"http://f-radiko.smartstream.ne.jp/{station_id}/_definst_/simul-stream.stream/playlist.m3u8"
        return self.gettoken.gen_temp_chunk_m3u8_url(url, self.token)

    def get_timefree_stream_url(self, station_id, ft_dt, to_dt):
        """互換用: タイムフリー再生URLと同等のURLを返す"""
        stream_url, _headers = self.get_timefree_playback_source(station_id, ft_dt, to_dt)
        return stream_url

    def get_timefree_playback_source(self, station_id, ft_dt, to_dt):
        """タイムフリー再生用 URL と必須ヘッダーを返す"""
        self.refresh_auth_session()
        ft = self._format_radiko_time(ft_dt)
        to = self._format_radiko_time(to_dt)
        try:
            chunklist_url = self._resolve_timefree_chunklist_url(station_id, ft, to)
            return chunklist_url, {"X-Radiko-AuthToken": self.token}
        except Exception as e:
            self.log.warning(f"timefree ts API failed, fallback to playlist_create_url(type=b): {e}")
            fallback_url = self._build_timefree_playlist_create_url(station_id, ft, to, stream_type="b", l_value=15)
            return fallback_url, {
                "X-Radiko-AuthToken": self.token,
                "X-Radiko-AreaId": self.area_id,
            }

    def get_timefree_playback_source_compat(self, station_id, ft_dt, to_dt):
        """タイムフリー再生互換URL(type=b)"""
        self.refresh_auth_session()
        ft = self._format_radiko_time(ft_dt)
        to = self._format_radiko_time(to_dt)
        fallback_url = self._build_timefree_playlist_create_url(station_id, ft, to, stream_type="c", l_value=15)
        return fallback_url, {
            "X-Radiko-AuthToken": self.token,
            "X-Radiko-AreaId": self.area_id,
        }

    def get_timefree_playback_source_with_seek(self, station_id, ft_dt, to_dt, seek_seconds=0, stream_type="b"):
        """seek位置を指定してタイムフリー再生URLとヘッダーを返す（playlist_create_url方式）"""
        self.refresh_auth_session()
        if seek_seconds is None:
            seek_seconds = 0
        seek_seconds = max(0, int(seek_seconds))
        total_duration = max(1, int((to_dt - ft_dt).total_seconds()))
        seek_seconds = min(seek_seconds, total_duration - 1)

        ft = self._format_radiko_time(ft_dt)
        to = self._format_radiko_time(to_dt)
        seek_dt = ft_dt + datetime.timedelta(seconds=seek_seconds)
        seek_ft = self._format_radiko_time(seek_dt)

        url = self._build_timefree_playlist_create_url(
            station_id,
            ft,
            to,
            stream_type=stream_type,
            l_value=15,
            seek_ft=seek_ft,
        )
        return url, {
            "X-Radiko-AuthToken": self.token,
            "X-Radiko-AreaId": self.area_id,
        }

    def get_timefree_recording_source(self, station_id, ft_dt, to_dt):
        """タイムフリー録音用 URL と必須ヘッダーを返す"""
        self.refresh_auth_session()
        ft = self._format_radiko_time(ft_dt)
        to = self._format_radiko_time(to_dt)
        duration_sec = int(max(15, (to_dt - ft_dt).total_seconds()))
        l_value = min(duration_sec, 8 * 3600)
        try:
            chunklist_url = self._resolve_timefree_chunklist_url(station_id, ft, to)
            return chunklist_url, {"X-Radiko-AuthToken": self.token}
        except Exception as e:
            self.log.warning(f"timefree ts API failed, fallback to playlist_create_url(type=c): {e}")
            fallback_url = self._build_timefree_playlist_create_url(station_id, ft, to, stream_type="c", l_value=l_value)
            return fallback_url, {
                "X-Radiko-AuthToken": self.token,
                "X-Radiko-AreaId": self.area_id,
            }

    def _resolve_timefree_chunklist_url(self, station_id, ft, to):
        """sample実装準拠で ts/playlist.m3u8 から chunklist URL を得る"""
        params = {
            "station_id": station_id,
            "l": "15",
            "ft": ft,
            "to": to,
        }
        headers = {
            "X-Radiko-AuthToken": self.token,
            "X-Radiko-App": "pc_html5",
            "X-Radiko-App-Version": "0.0.1",
            "X-Radiko-User": "dummy",
            "X-Radiko-Device": "pc",
        }
        candidates = [
            "https://radiko.jp/v2/api/ts/playlist.m3u8",
            "http://radiko.jp/v2/api/ts/playlist.m3u8",
        ]
        last_error = None
        for url in candidates:
            try:
                response = requests.get(url, params=params, headers=headers, timeout=10)
                response.raise_for_status()
                text = response.text
                m = re.search(r'https://radiko\.jp/v2/api/ts/chunklist/[a-zA-Z0-9_/\-]{1,200}\.m3u8', text)
                if m:
                    return m.group()
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("http") and ".m3u8" in line:
                        return line
                last_error = RuntimeError(f"timefree chunklist URL not found: {text[:200]}")
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(last_error)

    def _build_timefree_playlist_create_url(self, station_id, ft, to, stream_type="c", l_value=15, seek_ft=None):
        """playlist_create_url ベースのタイムフリーURLを構築"""
        playlist_base = self._get_timefree_playlist_create_base_url(station_id)
        lsid = secrets.token_hex(16)
        if not seek_ft:
            seek_ft = ft
        query = urllib.parse.urlencode({
            "station_id": station_id,
            "start_at": ft,
            "ft": ft,
            "seek": seek_ft,
            "end_at": to,
            "to": to,
            "l": str(int(l_value)),
            "lsid": lsid,
            "type": stream_type,
        })
        return f"{playlist_base}?{query}"

    def _get_timefree_playlist_create_base_url(self, station_id):
        url = f"https://radiko.jp/v3/station/stream/pc_html5/{station_id}.xml"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for item in root.findall(".//url"):
            if item.get("timefree") != "1":
                continue
            playlist = item.find("playlist_create_url")
            if playlist is not None and playlist.text:
                return playlist.text.strip()
        raise RuntimeError("playlist_create_url not found")

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
        return "http://radiko.jp/v3"

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
        """現在再生中の番組タイトルを返す"""
        try:
            # 方法1: 放送局IDを直接使用して番組情報を取得
            url = f"{self.getprogramlist()}/program/now/{id}.xml"
            self.log.debug(f"Trying direct station API for {id}: {url}")
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
            except requests.RequestException as e:
                self.log.warning(f"Direct station API failed for {id}: {e}")
                # 方法2: 都道府県コードを使用（フォールバック）
                return self._getNowProgramByArea(id)
                
            try:
                root = ET.parse(url)
                results = root.xpath(".//station")
                progs = root.xpath(".//progs")
            except Exception as e:
                self.log.warning(f"Failed to parse direct station XML for {id}: {e}")
                # 方法2: 都道府県コードを使用（フォールバック）
                return self._getNowProgramByArea(id)
                
            self.url = url
            self.progs = progs
            self.results = results
            self.response = response
            
            # 直接取得した場合、該当する放送局の番組情報を探す
            for result, prog in zip(results, progs):
                if result.get("id") == id:
                    try:
                        title_element = prog.xpath(".//title")
                        if title_element and title_element[0].text:
                            self.log.debug(f"Found program title via direct API for {id}: {title_element[0].text}")
                            return title_element[0].text
                    except Exception as e:
                        self.log.warning(f"Failed to extract title for station {id}: {e}")
                        continue
            
            # 見つからない場合は都道府県コードを使用
            self.log.debug(f"No program found via direct API for {id}, trying area-based method")
            return self._getNowProgramByArea(id)
                
        except Exception as e:
            self.log.error(f"Unexpected error in getNowProgram: {e}")
            return None

    def _getNowProgramByArea(self, id):
        """都道府県コードを使用して番組情報を取得（フォールバック）"""
        try:
            title_dic = {} #stationidをキー、番組名を値とする辞書
            if id not in self.values:
                self.log.warning(f"Station ID {id} not found in values")
                return None
                
            jp_number = self.values[id]
            #引数の都道府県コードをつけてリクエスト
            url = f"{self.getprogramlist()}/program/now/{jp_number}.xml"
            self.log.debug(f"Trying area-based API for {id} (area: {jp_number}): {url}")
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
            except requests.RequestException as e:
                self.log.error(f"Failed to fetch program data by area: {e}")
                return None
                
            try:
                root = ET.parse(url)
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
        url = f'http://radiko.jp/v3/feed/pc/noa/{id}.xml'
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        root = ET.parse(url)
        items = root.xpath(".//item")
        
        if items and len(items) > 0:
            title = items[0].get("title", "")
            artist = items[0].get("artist", "")
            if title and artist:
                return f"{artist} - {title}"
            elif title:
                return title
            else:
                return ""
        else:
            return ""

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
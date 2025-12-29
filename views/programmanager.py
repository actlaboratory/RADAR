#programmanager

import re
import lxml.etree as ET
from logging import getLogger
import requests
import constants
import datetime
import tcutil
from views import token

class ProgramManager:
    def __init__(self):
        self.log=getLogger("%s.%s" % (constants.LOG_PREFIX,"ProgramManager"))
        self.log.debug("created!")
        self.jpCode()
        self.tcutil = tcutil.CalendarUtil()

    def getArea(self):
        """エリアを判定する"""
        self.gettoken = token.Token()
        res = self.gettoken.auth1()
        ret = self.gettoken.get_partial_key(res)
        self.token = ret[1]
        self.partialkey = ret[0]
        self.gettoken.auth2(self.partialkey, self.token )
        area = self.gettoken.area #エイラを取得
        before = re.findall("\s", area)
        replace = area.replace(before[0], ",") #スペースを文字列置換で,に置き換える
        values = replace.split(",") #戻り地をリストにする
        result = values[2]
        return result

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
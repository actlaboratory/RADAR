#programmanager

import xml.etree.ElementTree as ET
import re
import lxml.etree
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
        year = date[:4]
        lists = date.split(",")
        if len(lists[1]) == 1:
            lists[1] = f"0{lists[1]}"
        if len(lists[2]) == 1:
            lists[2] = f"0{lists[2]}"
        formatted_date = f"{lists[0]}{lists[1]}{lists[2]}"
        url = f"{self.getprogramlist()}/program/station/date/{formatted_date}/{id}.xml"

        # XMLデータを取得
        response = requests.get(url)
        xml_data = response.content
        # XMLを解析
        self.root = ET.fromstring(xml_data)

    def gettitle(self):
        title_elements = self.root.findall(".//title")
        titles = [title.text for title in title_elements]
        return titles

    def getpfm(self):
        pfm_elements = self.root.findall(".//pfm")
        names = [pfm.text for pfm in pfm_elements]
        return names

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
            title_dic = {} #stationidをキー、番組名を値とする辞書
            if id not in self.values:
                self.log.warning(f"Station ID {id} not found in values")
                return None
                
            jp_number = self.values[id]
            #引数の都道府県コードをつけてリクエスト
            url = f"{self.getprogramlist()}/program/now/{jp_number}.xml"
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
            except requests.RequestException as e:
                self.log.error(f"Failed to fetch program data: {e}")
                return None
                
            try:
                root = lxml.etree.parse(url)
                results = root.xpath(".//station")
                progs = root.xpath(".//progs")
            except Exception as e:
                self.log.error(f"Failed to parse XML: {e}")
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
                return title_dic[id]
            else:
                self.log.warning(f"No program found for station ID {id}")
                return None
                
        except Exception as e:
            self.log.error(f"Unexpected error in getNowProgram: {e}")
            return None

    def getnowProgramPfm(self, id):
        """現在放送中の番組の出演者を返す"""
        try:
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
                return None
        except Exception as e:
            self.log.error(f"Unexpected error in getnowProgramPfm: {e}")
            return None

    def getNowProgramDsc(self, id):
        """番組の説明を取得して返す"""
        try:
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
        results = []
        prog_elements = self.root.findall(".//prog")
        prog_ftl = [ftl.get("ftl") for ftl in prog_elements]
        return prog_ftl

    def get_tol(self):
        results = []
        prog_elements = self.root.findall(".//prog")
        prog_tol = [tol.get("tol") for tol in prog_elements]
        return prog_tol

    def get_onair_music(self, id):
        url = f'http://radiko.jp/v3/feed/pc/noa/{id}.xml'
        response = requests.get(url)
        xml_data = response.content
        root = lxml.etree.parse(url)
        items = root.xpath(".//item")
        title = items[0].get("title")
        artist = items[0].get("artist")
        music = f"{artist}:{title}"

    def getDescriptions(self):
        desc_elements = self.root.findall(".//desc")
        descriptions = [description.text for description in desc_elements]
        return descriptions
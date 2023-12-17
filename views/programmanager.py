
import xml.etree.ElementTree as ET
import re
import lxml.etree
from logging import getLogger
import requests
import constants
import datetime

class ProgramManager:
    def __init__(self):
        self.log=getLogger("%s.%s" % (constants.LOG_PREFIX,"ProgramManager"))
        self.log.info("initialized!")
        self.jpCode()

    def getprogramlist(self):
        return "http://radiko.jp/v3"

    def getTodayProgramList(self, id):
        dt = datetime.datetime.now().date()
        dtstring = str(dt).replace("-", "")
        url = f"{self.getprogramlist()}/program/station/date/{dtstring}/{id}.xml"
        # XMLデータを取得
        response = requests.get(url)
        xml_data = response.content
        # XMLを解析
        self.root = ET.fromstring(xml_data)
        #debug
        date = self.root.find(".//date")
        self.log.debug(date.text)


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
        title_dic = {} #stationidをキー、番組名を値とする辞書
        if id in self.values:
            jp_number = self.values[id]
        #引数の都道府県コードをつけてリクエスト
        url = f"{self.getprogramlist()}/program/now/{jp_number}.xml"
        response = requests.get(url)
        xml_data = response.content
        root = lxml.etree.parse(url)
        results = root.xpath(".//station")
        progs = root.xpath(".//progs")
        self.progs = progs
        self.results = results
        for result,title in zip(results,progs):
            title_dic[result.get("id")] = title.xpath(".//title")[0].text

        #stationidに該当する番組名を返す
        if id in title_dic:
            return title_dic[id]

    def getnowProgramPfm(self, id):
        """現在放送中の番組の出演者を返す"""
        pfm_dic = {}
        for result,pfm in zip(self.results,self.progs):
            pfm_dic[result.get("id")] = pfm.xpath(".//pfm")[0].text

        if id in pfm_dic:
            return pfm_dic[id]

    def getProgramDsc(self, id):
        dsc_dic = {}
        """番組の説明を取得して返す"""

        for result,dsc in zip(self.results,self.progs):
            str = dsc.xpath(".//desc")[0].text
            if str is not None:
                dsc_dic[result.get("id")] = re.sub(re.compile('<.*?>'), '', str) #htmlタグを除去

        if id in dsc_dic:
            return dsc_dic[id]

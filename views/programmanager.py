
import xml.etree.ElementTree as ET
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
        """現在再生中の番組を取得"""
        lists = [] #stationidを格納
        dic = {"title":{}}
        if id in self.values:
            jp_number = self.values[id]
        #リクエスト
        url = f"{self.getprogramlist()}/program/now/{jp_number}.xml"
        response = requests.get(url)
        xml_data = response.content
        root = ET.fromstring(xml_data)
        for r in root:
            for s in r:
                lists.append(s.attrib["id"])

        title_elements = root.findall(".//title") #番組の名前
        pfm_elements = root.findall(".//pfm") #出演者の名前

        #リスト化
        programtitle = [title.text for title in title_elements]
        PFM = [pfm.text for pfm in pfm_elements]
        for station,title in zip(lists,programtitle):
            dic["title"][station] = title
        print(dic)
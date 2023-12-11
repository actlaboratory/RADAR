
import xml.etree.ElementTree as ET
from logging import getLogger
import requests
import constants
import datetime

class ProgramManager:
    def __init__(self):
        self.log=getLogger("%s.%s" % (constants.LOG_PREFIX,"ProgramManager"))
        self.log.info("initialized!")

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
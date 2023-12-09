#programmanager
from urllib import request
import xml.etree.ElementTree as ET

class ProgramManager:
    def __init__(self):
            print("created")
    def getdata(self, id):
        url = f"http://radiko.jp/v3/program/station/today/{id}.xml"
        req = request.Request(url) 
        with request.urlopen(req) as response:
            xml_data = response.read().decode() #デフォルトではbytesオブジェクトなので文字列へのデコードが必要
            parsed = ET.fromstring(xml_data)
            for station in parsed:
                for i in station:
                    for progs in i[1]:
                        return progs.find("title")
from urllib import request

def debug():
    print("started")
    with open("result.txt", "w", encoding="utf-8") as f:
        url = "https://radiko.jp/v3/station/region/full.xml"
        r = request.Request(url)
        with request.urlopen(r) as res:
            xml_text = res.read().decode()
            f.write(xml_text)
            print("done")

if __name__ == "__main__":
    debug()
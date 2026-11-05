import secrets
import random
import time
from urllib.parse import urlparse

class Window:
    title: str
    url: str
    hash: str

    def __init__(self, title: str, url: str):
        self.title = title
        u = urlparse(url)
        self.origin = "%s://%s" % (u.scheme, u.netloc)
        self.url = url
        self.hash = secrets.token_hex(16)

class Fingerprint:
    def __init__(self,
                 user_agent,
                 content_type_value="application/x-www-form-urlencoded; charset=UTF-8",
                 accept_language_value="en-US,en;q=0.9",
                 extra_headers={},
                 sec_fetch=True,
                 protochain_hash="5d76839801bc5904a4f12f1731a7b6d1",
                 jsbd_gen=None,
                 DNT="unknown",
                 L="en-US",
                 D=24,
                 PR=1,
                 S="1920,1080",
                 AS="1920,1040",
                 TO=None,
                 SS=True,
                 LS=True,
                 IDB=True,
                 B=False,
                 ODB=True,
                 CPUC="unknown",
                 PK="Win32",
                 CFP=None,
                 FR=False,
                 FOS=False,
                 FB=False,
                 JSF="Arial,Arial Black,Arial Narrow,Book Antiqua,Bookman Old Style,Calibri,Cambria,Cambria Math,Century,Century Gothic,Century Schoolbook,Comic Sans MS,Consolas,Courier,Courier New,Garamond,Georgia,Helvetica,Impact,Lucida Bright,Lucida Calligraphy,Lucida Console,Lucida Fax,Lucida Handwriting,Lucida Sans,Lucida Sans Typewriter,Lucida Sans Unicode,Microsoft Sans Serif,Monotype Corsiva,MS Gothic,MS PGothic,MS Reference Sans Serif,MS Sans Serif,MS Serif,Palatino Linotype,Segoe Print,Segoe Script,Segoe UI,Segoe UI Light,Segoe UI Semibold,Segoe UI Symbol,Tahoma,Times,Times New Roman,Trebuchet MS,Verdana,Wingdings,Wingdings 2,Wingdings 3",
                 P="Chrome PDF Plugin,Chrome PDF Viewer,Native Client",
                 T="0,false,false",
                 H="8",
                 SWF=False):
        self.user_agent = user_agent
        self.content_type_value = content_type_value
        self.accept_language_value = accept_language_value
        self.extra_headers = extra_headers
        self.sec_fetch = sec_fetch
        self.jsbd_gen = jsbd_gen
        
        self.fp_hash = secrets.token_hex(16)
        self.protochain_hash = protochain_hash

        self.DNT = DNT
        self.L = L
        self.D = D
        self.PR = PR
        self.S = S
        self.AS = AS
        self.TO = TO or (60 * random.randint(-6, 6))
        self.SS = SS
        self.LS = LS
        self.IDB = IDB
        self.B = B
        self.ODB = ODB
        self.CPUC = CPUC
        self.PK = PK
        self.CFP = CFP or random.randint(-35492349, 2395492344)
        self.FR = FR
        self.FOS = FOS
        self.FB = FB
        self.JSF = JSF
        self.P = P
        self.T = T
        self.H = H
        self.SWF = SWF

    def ife(self):
        # We simulate a static hash now since Arkose has patched the old JS method
        # This provides a realistic looking string that matches modern browsers
        return "b042d781029178c9750ec3041fbb16e4562b3188fc6eb7b37813d2c408292571f1eaa04923e1c4f0d731a683dd1dc77"

    def get_features(self):
        return [
            "DNT:%s" % self.DNT,
            "L:%s" % self.L,
            "D:%d" % self.D,
            "PR:%s" % str(self.PR),
            "S:%s" % self.S,
            "AS:%s" % self.AS,
            "TO:%d" % self.TO,
            "SS:%s" % str(self.SS).lower(),
            "LS:%s" % str(self.LS).lower(),
            "IDB:%s" % str(self.IDB).lower(),
            "B:%s" % str(self.B).lower(),
            "ODB:%s" % str(self.ODB).lower(),
            "CPUC:%s" % self.CPUC,
            "PK:%s" % self.PK,
            "CFP:%d" % self.CFP,
            "FR:%s" % str(self.FR).lower(),
            "FOS:%s" % str(self.FOS).lower(),
            "FB:%s" % str(self.FB).lower(),
            "JSF:%s" % self.JSF,
            "P:%s" % self.P,
            "T:%s" % self.T,
            "H:%s" % str(self.H),
            "SWF:%s" % str(self.SWF).lower(),
        ]

    def get_jsbd(self, window):
        return (self.jsbd_gen and self.jsbd_gen(window)) or {
            "HL": random.randint(1, 5),
            "NCE": True,
            "DT": window.title,
            "NWD": "false",
            "DMTO": random.randint(1, 10),
            "DOTO": random.randint(1, 10)
        }

    def get_timestamp(self):
        return time.time()

    def get_float(self):
        return random.uniform(0, 1)

    def get_xy(self) -> list:
        # Simulates a real mouse moving and clicking on the button
        start_pos = [random.randint(110, 130), random.randint(240, 260)]
        button_size = [90, 28]
        new_pos = [
            start_pos[0] + random.randint(1, button_size[0]),
            start_pos[1] + random.randint(1, button_size[1])]
        return new_pos

    def get_headers(self, host, method, data, origin, referer,
                    cache_control=False, xml=False,
                    fetch_site=None, fetch_mode=None, fetch_dest=None,
                    timestamp=False):
        headers = {}
        headers["Host"] = host
        headers["Connection"] = "keep-alive"
        if data:
            headers["Content-Length"] = str(len(data))
        headers["User-Agent"] = self.user_agent
        if data:
            headers["Content-Type"] = self.content_type_value
        headers["Accept"] = "*/*"
        if cache_control:
            headers["Cache-Control"] = "no-cache"
        if xml:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if origin:
            headers["Origin"] = origin
            
        if self.sec_fetch:
            headers["Sec-Fetch-Site"] = fetch_site
            headers["Sec-Fetch-Mode"] = fetch_mode
            headers["Sec-Fetch-Dest"] = fetch_dest
            
        for hk, hv in self.extra_headers.items():
            headers[hk] = hv
            
        headers["Referer"] = referer
        headers["Accept-Encoding"] = "gzip, deflate, br"
        headers["Accept-Language"] = self.accept_language_value
        if timestamp:
            ts = self.get_timestamp() * 100000
            headers["Cookie"] = "timestamp=%d" % ts
        return headers
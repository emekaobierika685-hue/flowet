import json
import random
import string
import hashlib
import time
import math
from base64 import b64encode, b64decode
from fingerprint import Fingerprint, Window
from urllib.parse import urlparse, urlencode
from Crypto.Cipher import AES
import tls_client

API_BREAKER = {
    "method_1": lambda x,y: dict(x=y,y=x),
    "method_2": lambda x,y: dict(x=y,y=(y+x)*x),
    "method_3": lambda x,y: dict(a=x,b=y),
    "method_4": lambda x,y: [x,y],
    "method_5": lambda x,y: list(map(math.sqrt, [y,x])),
}

def cryptojs_encrypt(data: (str, bytes), key: str) -> str:
    data = data + chr(16-len(data)%16)*(16-len(data)%16)
    salt = b"".join(random.choice(string.ascii_lowercase).encode() for x in range(8))
    salted, dx = b"", b""
    while len(salted) < 48:
        dx = hashlib.md5(dx+key.encode()+salt).digest()
        salted += dx
    key = salted[:32]
    iv = salted[32:32+16]
    aes = AES.new(key, AES.MODE_CBC, iv)
    encrypted_data = {"ct": b64encode(aes.encrypt(data.encode())).decode("utf-8"), "iv": iv.hex(), "s": salt.hex()}
    return json.dumps(encrypted_data, separators=(',', ':'))

def cryptojs_decrypt(data: (str, bytes), key: str) -> bytes:
    data = json.loads(data)
    dk = key.encode()+bytes.fromhex(data["s"])
    md5 = [hashlib.md5(dk).digest()]
    result = md5[0]
    for i in range(1, 3+1):
        md5.insert(i, hashlib.md5((md5[i-1]+dk)).digest())
        result += md5[i]
    aes = AES.new(result[:32], AES.MODE_CBC, bytes.fromhex(data["iv"]))
    data = aes.decrypt(b64decode(data["ct"]))
    return data

class Proxy:
    def __init__(self, proxy, type="http"):
        u = urlparse("https://%s" % proxy)
        self.type = type
        self.host = u.hostname
        self.port = u.port
        self.username = u.username
        self.password = u.password

    def __repr__(self):
        auth = ""
        if self.username:
            auth = "%s:%s@" % (self.username, self.password)
        return "%s%s:%s" % (auth, self.host, self.port)

class BannedProxy(Exception): pass
class BadGameTypeOrVariant(Exception): pass

class FunCaptchaChallenge:
    def __init__(self, session, start_time, full_token, session_token, region, meta, lang, analytics_tier, secure=None):
        self.resubmitted = False
        self.session = session
        self.start_time = start_time
        self.whitelisted_types = self.session.whitelisted_types
        self.proxy = self.session.proxy
        self.service = session.service
        self.window = session.window
        self.fp = session.fp
        self.full_token = full_token
        self.session_token = session_token
        self.region = region
        self.meta = meta
        self.lang = lang
        self.analytics_tier = analytics_tier
        self.guesses = []
        self.image_ekey = None
        self.request_data = {}
        self.game_type = None

        self.load()

        self.request_data["sc"] = self.fp.get_xy()
        if self.encrypted_mode:
            self.get_encryption_key()

    def __repr__(self):
        return "<Challenge Type:%d, Degree:%d>" % (self.game_type, self.rotate_degree)

    def get_session_url(self):
        params = "&".join([q.replace("#", "%23") for q in self.full_token.split("|")])
        return "https://%s/fc/gc/?token=%s" % (self.service, params)

    def add_requested_headers(self, headers):
        ts = str(int(self.fp.get_timestamp() * 100000))
        rdata = dict() if self.game_type == 3 else self.request_data
        data = cryptojs_encrypt(json.dumps(rdata, separators=(',', ':')), "REQUESTED%sID" % self.session_token)
        headers["X-NewRelic-Timestamp"] = ts
        headers["X-Requested-ID"] = data

    def load(self):
        data = urlencode({
            "sid": self.region, "token": self.session_token, "analytics_tier": self.analytics_tier,
            "data[status]": "init", "lang": self.lang, "render_type": "canvas"
        })
        headers = self.fp.get_headers(
            host=self.service, method="POST", data=data, cache_control=True, xml=True,
            origin="https://%s" % self.service, referer=self.get_session_url(), timestamp=True,
            fetch_site="same-origin", fetch_mode="cors", fetch_dest="empty"
        )
        self.add_requested_headers(headers)
        
        # Uses tls_client to bypass TLS fingerprinting
        resp = self.session.http_session.post(f"https://{self.service}/fc/gfct/", headers=headers, data=data)
        if resp.status_code != 200:
            raise BannedProxy(f"HTTP {resp.status_code}")
        
        data = resp.json()
        if "error" in data:
            raise BannedProxy(data["error"])
            
        self.challenge_token = data["challengeID"]
        self.game_type = data["game_data"]["gameType"]
        self.game_variant = data["game_data"].get("game_variant")
        self.waves = data["game_data"]["waves"]
        self.encrypted_mode = data["game_data"]["customGUI"].get("encrypted_mode") == 1
        self.image_urls = data["game_data"]["customGUI"]["_challenge_imgs"]
        self.api_breaker = data["game_data"]["customGUI"].get("api_breaker")
        
        self.rotate_degree = float(int(data["game_data"]["customGUI"]["_guiFontColr"].replace("#", "")[-3:], 16)) if "_guiFontColr" in data["game_data"]["customGUI"] else None
        if self.rotate_degree and self.rotate_degree > 113: 
            self.rotate_degree = self.rotate_degree/10

    def get_encryption_key(self):
        data = urlencode({"session_token": self.session_token, "sid": self.region, "game_token": self.challenge_token})
        headers = self.fp.get_headers(
            host=self.service, method="POST", data=data, cache_control=True, xml=True,
            origin="https://%s" % self.service, referer=self.get_session_url(),
            fetch_site="same-origin", fetch_mode="cors", fetch_dest="empty", timestamp=True
        )
        self.add_requested_headers(headers)
        resp = self.session.http_session.post(f"https://{self.service}/fc/ekey/", headers=headers, data=data)
        self.image_ekey = resp.json().get("decryption_key")

    def check_answer(self, guess=None, bypass=False):
        if guess:
            if not "dc" in self.request_data: self.request_data["dc"] = self.fp.get_xy()
            if len(self.guesses)+1 >= self.waves:
                self.request_data["ech"] = "{:.2f}".format(guess) if self.game_type == 1 else guess
            if self.game_type == 1: self.guesses.append(guess)
            elif self.game_type == 3: self.guesses.append(API_BREAKER[self.api_breaker](*guess))
            
        gdata = ",".join(map("{:.2f}".format, self.guesses)) if self.game_type == 1 else json.dumps(self.guesses, separators=(',', ':'))
        data = urlencode({
            "sid": self.region, "game_token": self.challenge_token, "session_token": self.session_token,
            "guess": cryptojs_encrypt(gdata, self.session_token), "analytics_tier": self.analytics_tier
        })
        headers = self.fp.get_headers(
            host=self.service, method="POST", data=data, cache_control=True, xml=True,
            origin="https://%s" % self.service, referer=self.get_session_url(),
            fetch_site="same-origin", fetch_mode="cors", fetch_dest="empty", timestamp=True
        )
        self.add_requested_headers(headers)
        resp = self.session.http_session.post(f"https://{self.service}/fc/ca/", headers=headers, data=data)
        
        data = resp.json()
        if data.get("decryption_key"): self.image_ekey = data["decryption_key"]
        return data.get("solved")

    @property
    def images(self):
        for image_url in self.image_urls:
            resp = self.session.http_session.get(image_url)
            data = resp.content
            if self.image_ekey:
                image = cryptojs_decrypt(data, self.image_ekey)
            else:
                image = data
            try:
                image.decode("ascii")
                image = b64decode(image)
            except: pass
            yield image

class FunCaptchaSession:
    def __init__(self, public_key, service_url, window, fingerprint, analytics=False, proxy=None, whitelisted_types=[], whitelisted_variants=[]):
        self.public_key = public_key
        self.service = urlparse(service_url).netloc
        self.window = window
        self.analytics = analytics
        self.fp = fingerprint
        self.proxy = proxy
        self.whitelisted_types = whitelisted_types
        self.whitelisted_variants = whitelisted_variants
        
        # Initialize tls_client to spoof Chrome TLS
        self.http_session = tls_client.Session(
            client_identifier="chrome_120",
            random_tls_extension_order=True
        )
        
        if self.proxy:
            proxy_url = f"http://{self.proxy.username}:{self.proxy.password}@{self.proxy.host}:{self.proxy.port}" if self.proxy.username else f"http://{self.proxy.host}:{self.proxy.port}"
            self.http_session.proxies = {"http": proxy_url, "https": proxy_url}

    def close_conns(self):
        self.http_session.close()

    def get_bda(self):
        bda = [
            dict(key="api_type", value="js"),
            dict(key="p", value=1),
            dict(key="f", value=self.fp.fp_hash),
            dict(key="n", value=b64encode(str(int(self.fp.get_timestamp())).encode("utf-8")).decode("utf-8")),
            dict(key="wh", value="%s|%s" % (self.window.hash, self.fp.protochain_hash)),
            dict(key="fe", value=self.fp.get_features()),
            dict(key="ife_hash", value=self.fp.ife()),
            dict(key="cs", value=1),
            dict(key="jsbd", value=json.dumps(self.fp.get_jsbd(self.window), separators=(',', ':')))
        ]
        timeframe = int(self.fp.get_timestamp() - (self.fp.get_timestamp() % 21600))
        key = self.fp.user_agent + str(timeframe)
        data = json.dumps(bda, separators=(',', ':'))
        return b64encode(cryptojs_encrypt(data, key).encode("utf-8")).decode("utf-8")

    def get_challenge(self):
        start_time = time.time()
        data = urlencode({
            "bda": self.get_bda(), "public_key": self.public_key, "site": self.window.origin,
            "userbrowser": self.fp.user_agent, "simulate_rate_limit": 0, "simulated": 0,
            "language": "en", "rnd": self.fp.get_float()
        })
        headers = self.fp.get_headers(
            host=self.service, method="POST", data=data,
            origin=self.window.origin, referer=self.window.url,
            fetch_site="cross-site", fetch_mode="cors", fetch_dest="empty"
        )
        
        resp = self.http_session.post(f"https://{self.service}/fc/gt2/public_key/{self.public_key}", headers=headers, data=data)
        
        if resp.status_code != 200:
            raise BannedProxy("This proxy/IP address is banned")

        ch_data = resp.json()
        token_data = {k:v for k,v in (f.split("=") for f in ("token="+ch_data["token"]).split("|"))}
        
        return FunCaptchaChallenge(
            session=self, start_time=start_time, full_token=ch_data["token"],
            session_token=token_data["token"], region=token_data["r"],
            meta=int(token_data["meta"]), lang=token_data.get("lang"),
            analytics_tier=int(token_data["at"]), secure="s" in token_data and int(token_data["s"])
        )
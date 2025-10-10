# skinmarketanalyzer.py

from typing import Callable, Optional
import re, sys, json, time, random, threading, webbrowser
import requests
from html import unescape
from urllib.parse import quote_plus, unquote
import urllib
import urllib.parse

from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import webbrowser

from PySide6.QtCore import (
    Qt, QPoint, QSize, QUrl, QObject, Signal, Slot, QBuffer, QByteArray, QThread
)
from PySide6.QtGui import (
    QPalette, QColor, QIcon, QAction, QPixmap, QImageReader
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QFileDialog,
    QHeaderView, QToolBar, QMessageBox, QLineEdit, QFrame, QAbstractItemView,
    QSpinBox, QDoubleSpinBox, QFormLayout , QMenu, QToolButton   # <-- eklendi
)

from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


# -------------------- Genel sabitler --------------------
COLUMNS = [
    "Görsel", "İsim", "Kalite", "Site Fiyatı", "Pazar Fiyatı",
    "Sipariş Fiyatı", "Kâr Oranı (%)", "Kâr Miktarı"
]

# UI modes
UI_MODES = {
    "dark": "Karanlık",
    "light": "Aydınlık",
    "grad_dark": "Mor",
    "grad_light": "Gradyan (Açık)",
    "asimov": "Asiimov"
}


MAX_ITEMS = 200

PLACEHOLDER_JSON = """{
  "items": [
    {
      "id": 939,
      "name": "FAMAS | Hexane (Minimal Wear)",
      "sell_price": 3.85,
      "image_url": "https%3A%2F%2Fcommunity.akamai.steamstatic.com%2Feconomy%2Fimage%2F-9a81dlWLwJ2UUGcVs_nsVtzdOEdtWwKGZZLQHTxDZ7I56KU0Zwwo4NUX4oFJZEHLbXH5ApeO4YmlhxYQknCRvCo04DEVlxkKgposLuoKhRf2-r3czFX6dSzjL-HnvD8J_XXlzIH7ZB02bqZp4rwiwCy_UJvZG7yJYCde1NtaVvWqAK4weq51JW4ot2Xni4H79h_%2F360fx360f",
      "quality": "Minimal Wear",
      "color": "#4b69ff",
      "stattrak": false,
      "type": "7"
    },
    {
      "id": 3139,
      "name": "P250 | Steel Disruption (Factory New)",
      "sell_price": 3.85,
      "image_url": "https%3A%2F%2Fcommunity.akamai.steamstatic.com%2Feconomy%2Fimage%2F-9a81dlWLwJ2UUGcVs_nsVtzdOEdtWwKGZZLQHTxDZ7I56KU0Zwwo4NUX4oFJZEHLbXH5ApeO4YmlhxYQknCRvCo04DEVlxkKgpopujwezhh3szMdS1D-NizmpOOqOT9P63UhFRd4cJ5nqeV9trw2gbm-Rc5NWvwLYacegZraQyBqVi2kLi80MO4tc-bzSQ3uyl0-z-DyEXRh4R0%2F360fx360f",
      "quality": "Factory New",
      "color": "#4b69ff",
      "stattrak": false,
      "type": "1"
    }
  ]
}
"""

STEAM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://steamcommunity.com/market/",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}

# ------------ Pricempire yardımcıları ------------
WEAR_MAP = {
    "Factory New": "factory-new",
    "Minimal Wear": "minimal-wear",
    "Field-Tested": "field-tested",
    "Well-Worn": "well-worn",
    "Battle-Scarred": "battle-scarred",
}
WEARS = list(WEAR_MAP.keys())
def _strip_word_souvenir(s: str) -> str:
    # 'Souvenir ' öneki veya metin içindeki bağımsız 'souvenir' kelimesini sil
    s = re.sub(r"(?i)^\s*souvenir\s+", "", s)         # baştaki "Souvenir "
    s = re.sub(r"(?i)\bsouvenir\b", "", s)            # kalan bağımsız kelime
    return re.sub(r"\s{2,}", " ", s).strip()          # fazla boşlukları temizle

def is_glove_item(name: str, weapon: str = "", skin: str = "") -> bool:
    txt = f"{name} {weapon} {skin}".lower()
    # minimum kelimeler: glove, gloves, hand wraps
    return ("glove" in txt) or ("gloves" in txt) or ("hand wraps" in txt)
def is_souvenir_item(name: str) -> bool:
    return "souvenir" in name.lower()


def slugify(s: str) -> str:
    s = s.lower()
    s = s.replace("™", "")
    # apostrofları tamamen kaldır (Chantico's -> chanticos)
    s = s.replace("'", "").replace("\u2019", "")  # \u2019 = ’
    # harf/rakam/boşluk/tire dışındakileri boşluğa çevir
    s = re.sub(r"[^a-z0-9\s-]", " ", s)
    # boşlukları tek tireye çevir
    s = re.sub(r"\s+", "-", s.strip())
    # birden fazla tireyi teke indir
    s = re.sub(r"-{2,}", "-", s)
    return s

def parse_item_name(name: str) -> dict:
    stat = ("stattrak" in name.lower() or "stattrak™" in name.lower() or "stattrak\u2122" in name.lower())
    s = name.replace("StatTrak™", "").replace("StatTrak\u2122", "").replace("StatTrak", "").strip()
    wear = None
    m = re.search(r"\(([^)]+)\)$", s)
    if m and m.group(1) in WEARS:
        wear = m.group(1)
        s = s[:m.start()].strip()
    parts = [p.strip() for p in s.split("|", 1)]
    weapon = parts[0] if parts else ""
    skin = parts[1] if len(parts) > 1 else ""
    return {"weapon": weapon, "skin": skin, "wear": wear, "stat": stat}


# ---- Agent helpers (added) ----
AGENT_TEAM_TOKENS = {
    "sabre","elite crew","the professionals","phoenix","gendarmerie nationale",
    "fbi swat","s.w.a.t","swat","fbi hrt","gsg-9","gendarmerie","usaf tacp","sneaky beaky",
    "sasco","sas","nzsas","seal","navi seals","nswc seal","jungle rebel","ground rebel",
    "guerrilla warfare","pirate","professor","dragomir","rezan","mccoy","judge","ground",
    "professionals","elite","crew","tacp","gendarmerie nationale"
}

def _canon(s: str) -> str:
    return slugify(s)

def is_probably_agent(weapon: str, skin: str) -> bool:
    # Heuristic: if left side is NOT a known weapon and right side looks like a team/faction -> agent
    w = _canon(weapon)
    known = {
        "ak-47","m4a1-s","m4a4","awp","desert-eagle","glock-18","usp-s","p250",
        "five-seven","cz75-auto","tec-9","p2000","dual-berettas","r8-revolver",
        "famas","galil-ar","sg-553","aug","ssg-08","scar-20","g3sg1",
        "mac-10","mp9","mp7","mp5-sd","p90","ump-45","pp-bizon","bizon",
        "nova","xm1014","mag-7","sawed-off","m249","negev",
        "karambit","bayonet","m9-bayonet","butterfly-knife","talon-knife","skeleton-knife",
        "stiletto-knife","falchion-knife","shadow-daggers","gut-knife","bowie-knife",
        "huntsman-knife","paracord-knife","survival-knife","ursus-knife","navaja-knife",
        "nomad-knife","classic-knife","kukri-knife","daggers","karambit-knife","flip-knife"
    }
    if w in known:
        return False
    # If weapon side contains obvious agent indicators or skin side looks like a faction/team, treat as agent
    right = skin.lower()
    if any(tok in right for tok in AGENT_TEAM_TOKENS):
        return True
    # If weapon contains quotes or human names, also likely agent
    if any(ch in weapon for ch in ["'", "’"]):
        return True
    # As a fallback: weapon has 2+ words and none of them are weapon names → likely agent
    return True if (len(weapon.split()) >= 2 and w not in known) else False

def build_agent_slug(name: str) -> str:
    # "Xxx | Team Yyy" -> "xxx-team-yyy" (Pricempire usually uses single '-' joiner; some items have '--' but single works for most)
    parts = [p.strip() for p in name.split("|", 1)]
    left = parts[0] if parts else name
    right = parts[1] if len(parts) > 1 else ""
    left_slug = slugify(left)
    right_slug = slugify(right)
    if right_slug:
        return f"{left_slug}-{right_slug}"
    return left_slug
# ---- End Agent helpers ----


def build_pricempire_url(name: str, quality: str = "", stattrak_hint: Optional[bool] = None) -> Optional[str]:
    info = parse_item_name(name)
    weapon, skin, wear, stat = info["weapon"], info["skin"], info["wear"], info["stat"]
    if not wear and quality in WEARS:
        wear = quality

    # ---- StatTrak yalnızca isimden gelsin ----
    stat_from_name = bool(re.search(r"\bstattrak\b", name, re.I)) or ("stattrak™" in name.lower()) or ("stattrak\u2122" in name.lower())
    stat = bool(stat_from_name)

    # ✅ SOUVENIR ÖNCELİK: Souvenir ise her zaman SKIN + souvenir-{wear}; StatTrak kapalı
    if is_souvenir_item(name):  # veya: if "souvenir" in name.lower():
        # weapon/skin içinden 'Souvenir' kelimesini tamamen çıkar
        clean_weapon = _strip_word_souvenir(weapon)
        clean_skin   = _strip_word_souvenir(skin)

        item_slug = slugify(f"{clean_weapon} {clean_skin}").replace("--", "-")
        wear_slug = WEAR_MAP.get(wear or "", None)
        if not item_slug or not wear_slug:
            return None
        return f"https://pricempire.com/cs2-items/skin/{item_slug}/souvenir-{wear_slug}"

    # ---- Eldiven kısa yolu (ajan heuristic'inden önce) ----
    if is_glove_item(name, weapon, skin):
        item_slug = slugify(f"{weapon} {skin}").replace("--", "-")
        wear_slug = WEAR_MAP.get(wear or "", None)
        if not item_slug or not wear_slug:
            return None
        return f"https://pricempire.com/cs2-items/glove/{item_slug}/{wear_slug}"

    # ---- Ajan tespiti ----
    if is_probably_agent(weapon, skin):
        agent_slug = build_agent_slug(name)
        return f"https://pricempire.com/cs2-items/agent/{agent_slug}"

    # ---- Normal skin akışı ----
    cat = "glove" if ("glove" in weapon.lower() or "gloves" in weapon.lower()) else "skin"
    item_slug = slugify(f"{weapon} {skin}").replace("--", "-")
    wear_slug = WEAR_MAP.get(wear or "", None)
    if not item_slug or not wear_slug:
        return None

    wear_part = f"stattrak-{wear_slug}" if (stat and cat == "skin") else wear_slug
    return f"https://pricempire.com/cs2-items/{cat}/{item_slug}/{wear_part}"


# ---- Pricempire link canonicalizer (added) ----
PE_ALLOWED_CATS = {"skin", "glove", "agent", "sticker",
    "tournament-sticker", "tournament-team-sticker-capsule",
    "container", "music-kit-box", "autograph-sticker"}
WEAR_SLUGS_CAN = {"factory-new","minimal-wear","field-tested","well-worn","battle-scarred"}
FINISH_TYPES = {"holo","foil","gold"}

_http_re = re.compile(r"(https?://pricempire\.com[^\s]+)", re.IGNORECASE)

def _extract_first_pricempire_url(s: str) -> str | None:
    if not s:
        return None
    m = _http_re.findall(s.replace("...tps://", " https://"))
    return m[-1] if m else None

def _clean_path(path: str) -> str:
    path = path.split("?")[0].split("#")[0].strip()
    path = re.sub(r"/{2,}", "/", path)
    return path.rstrip("/").strip()

def _canon_path(parts: list[str]) -> list[str]:
    out = []
    for p in parts:
        p = p.strip().lower()
        p = re.sub(r"-{2,}", "-", p)
        if p:
            out.append(p)
    return out

def _fix_wear_segment_canon(wear_seg: str) -> str | None:
    if not wear_seg:
        return None
    seg = wear_seg
    has_souv = seg.startswith("souvenir-")
    has_stat = seg.startswith("stattrak-")
    core = seg.split("-", 1)[1] if (has_souv or has_stat) else seg
    if core not in WEAR_SLUGS_CAN:
        return None
    if has_souv:
        return f"souvenir-{core}"
    if has_stat:
        return f"stattrak-{core}"
    return core

def pricempire_canonicalize(url_or_text: str) -> str | None:
    raw = _extract_first_pricempire_url(url_or_text)
    if not raw:
        return None
    try:
        pu = urllib.parse.urlsplit(raw)
    except Exception:
        return None
    if pu.netloc.lower() != "pricempire.com":
        return None

    path = _clean_path(pu.path)
    parts = _canon_path(path.split("/"))
    if len(parts) < 3 or parts[0] != "cs2-items" or parts[1] not in PE_ALLOWED_CATS:
        return None

    cat = parts[1]
    rest = parts[2:]
    if not rest:
        return None
    item_slug = rest[0]
    tail = rest[1] if len(rest) >= 2 else None

    new_parts = ["cs2-items", cat, item_slug]

    if cat in {"skin", "glove"}:
        wear = _fix_wear_segment_canon(tail or "")
        if wear:
            new_parts.append(wear)
    elif cat in {"sticker", "tournament-sticker"}:
        if tail in FINISH_TYPES:
            new_parts.append(tail)
    elif cat == "autograph-sticker":
        if tail == "gold":
            new_parts.append("gold")
    elif cat == "music-kit-box":
        if tail == "stattrak":
            new_parts.append("stattrak")
    # agent/container/capsule -> only slug

    canon_path = "/" + "/".join(new_parts)
    return urllib.parse.urlunsplit(("https", "pricempire.com", canon_path, "", ""))
# ---- End canonicalizer ----
def dark_palette():
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(30, 32, 34))
    pal.setColor(QPalette.WindowText, QColor(220, 220, 220))
    pal.setColor(QPalette.Base, QColor(24, 26, 27))
    pal.setColor(QPalette.AlternateBase, QColor(36, 38, 40))
    pal.setColor(QPalette.ToolTipBase, QColor(220, 220, 220))
    pal.setColor(QPalette.ToolTipText, QColor(30, 30, 30))
    pal.setColor(QPalette.Text, QColor(230, 230, 230))
    pal.setColor(QPalette.Button, QColor(45, 47, 50))
    pal.setColor(QPalette.ButtonText, QColor(235, 235, 235))
    pal.setColor(QPalette.BrightText, Qt.red)
    pal.setColor(QPalette.Highlight, QColor(76, 110, 245))
    pal.setColor(QPalette.HighlightedText, Qt.white)
    return pal
def light_palette():
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#F5F7FA"))
    pal.setColor(QPalette.WindowText, QColor("#202124"))
    pal.setColor(QPalette.Base, QColor("#FFFFFF"))
    pal.setColor(QPalette.AlternateBase, QColor("#EEF1F5"))
    pal.setColor(QPalette.ToolTipBase, QColor("#FFFFFF"))
    pal.setColor(QPalette.ToolTipText, QColor("#202124"))
    pal.setColor(QPalette.Text, QColor("#202124"))
    pal.setColor(QPalette.Button, QColor("#FFFFFF"))
    pal.setColor(QPalette.ButtonText, QColor("#202124"))
    pal.setColor(QPalette.BrightText, Qt.red)
    pal.setColor(QPalette.Highlight, QColor("#1769E0"))
    pal.setColor(QPalette.HighlightedText, Qt.white)
    return pal

ASIIMOV_ACCENT = "#df7116"


# -------------------- HTTP yardımcıları --------------------
def make_session():
    s = requests.Session()
    s.headers.update(STEAM_HEADERS)
    retry = Retry(
        total=3, connect=2, read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET'])
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

def parse_money_to_float(s: str) -> float | None:
    if not s:
        return None
    s = unescape(str(s)).strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(s)
    except:
        return None

def build_market_hash_name(item: dict) -> str:
    name = item.get("name", "") or ""
    stattrak = False
    if item.get("_raw") and isinstance(item["_raw"], dict):
        stattrak = bool(item["_raw"].get("stattrak", False))
    if stattrak and "StatTrak" not in name:
        name = f"StatTrak\u2122 {name}"
    return name


# -------------------- Global hız sınırlayıcı (token bucket) --------------------
class TokenBucket:
    """Global hız sınırı: rate (token/sn), burst (başlangıç tamponu)."""
    def __init__(self, rate: float, burst: int):
        self.rate = float(rate)
        self.capacity = int(burst)
        self.tokens = float(burst)
        self.lock = threading.Lock()
        self.last = time.time()

    def acquire(self, n: float = 1.0, stop_flag: Optional[Callable[[], bool]] = None):
        with self.lock:
            now = time.time()
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last = now
            if self.tokens < n:
                need = n - self.tokens
                wait = need / self.rate
            else:
                wait = 0.0
        end = time.time() + wait if wait > 0 else None
        while end and time.time() < end:
            if stop_flag and stop_flag():
                return False
            time.sleep(0.05)
        with self.lock:
            now2 = time.time()
            elapsed2 = now2 - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed2 * self.rate)
            self.last = now2
            if self.tokens >= n:
                self.tokens -= n
            else:
                self.tokens = 0.0
        return True


# -------------------- Görsel indirme (Qt Network) --------------------
class ImageLoader(QObject):
    image_ready = Signal(int, object)  # row, QPixmap | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._pending = {}
        try:
            self.manager.sslErrors.connect(self._on_ssl_errors)
        except Exception:
            pass

    def fetch(self, row: int, url: str):
        if not url:
            self.image_ready.emit(row, None)
            return
        self._start_request(row, QUrl(url), hop=0)

    def _start_request(self, row: int, qurl: QUrl, hop: int):
        req = QNetworkRequest(qurl)
        req.setRawHeader(b"User-Agent",
                         b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         b"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        req.setRawHeader(b"Referer", b"https://steamcommunity.com/")
        req.setRawHeader(b"Accept", b"image/avif,image/webp,image/*,*/*;q=0.8")
        try:
            req.setTransferTimeout(15000)
        except Exception:
            pass
        reply = self.manager.get(req)
        reply.finished.connect(lambda r=reply: self._on_finished(r))
        self._pending[reply] = (row, hop)

    @Slot()
    def _on_finished(self, reply: QNetworkReply):
        row, hop = self._pending.get(reply, (None, None))
        redir = None
        try:
            redir = reply.attribute(QNetworkRequest.RedirectionTargetAttribute)
        except Exception:
            pass
        if not redir:
            try:
                redir = reply.attribute(QNetworkRequest.RedirectTargetAttribute)
            except Exception:
                pass
        if redir:
            try:
                new_url = reply.url().resolved(QUrl(str(redir)))
                reply.deleteLater()
                self._pending.pop(reply, None)
                if hop is not None and hop < 5:
                    self._start_request(row, new_url, hop + 1)
                    return
            except Exception:
                pass

        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.image_ready.emit(row, None)
        else:
            data = reply.readAll()
            if data.size() <= 0:
                self.image_ready.emit(row, None)
            else:
                pix = QPixmap()
                if pix.loadFromData(data):
                    self.image_ready.emit(row, pix)
                else:
                    buf = QBuffer()
                    buf.setData(QByteArray(data))
                    buf.open(QBuffer.ReadOnly)
                    reader = QImageReader(buf)
                    img = reader.read()
                    if not img.isNull():
                        self.image_ready.emit(row, QPixmap.fromImage(img))
                    else:
                        self.image_ready.emit(row, None)
        reply.deleteLater()
        self._pending.pop(reply, None)

    def _on_ssl_errors(self, reply, errors):
        for e in errors:
            print("SSL ERR:", e.errorString())


# -------------------- Sayısal sıralama için NumericItem --------------------
class NumericItem(QTableWidgetItem):
    """Metin olarak gösterir, sayısal değere göre sıralar (NaN en sonda)."""
    def __init__(self, value: Optional[float]):
        txt = "" if value is None else f"{float(value):.2f}"
        super().__init__(txt)
        self._value = float(value) if value is not None else float("nan")
        self.setTextAlignment(Qt.AlignCenter)

    def value(self) -> float:
        return self._value

    def __lt__(self, other):
        if isinstance(other, NumericItem):
            a, b = self._value, other._value
            a_nan, b_nan = (a != a), (b != b)
            if a_nan and b_nan: return False
            if a_nan: return False  # NaN > number → NaN en sonda
            if b_nan: return True
            return a < b
        return super().__lt__(other)


# -------------------- Tablo --------------------
class PriceTable(QTableWidget):
    open_listing = Signal(str)  # mh

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.ExtendedSelection)
        self.setIconSize(QSize(96, 48))
        self.setWordWrap(False)
        self.setSortingEnabled(True)
        self.verticalHeader().setDefaultSectionSize(64)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        widths = [140, 260, 140, 120, 120, 120, 120, 120]
        for i, w in enumerate(widths):
            self.setColumnWidth(i, w)
        # "Sipariş Fiyatı" kolonunu gizle (artık kullanılmıyor)
        self.setColumnHidden(5, True)

    def clear_rows(self):
        self.setRowCount(0)

    def add_row(self, item: dict):
        row = self.rowCount()
        self.insertRow(row)

        img_label = QLabel()
        img_label.setFixedSize(128, 64)
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setText("Yükleniyor…")
        img_label.setStyleSheet("QLabel { border: 1px dashed #555; }")
        self.setCellWidget(row, 0, img_label)

        def cell(text, align=Qt.AlignVCenter | Qt.AlignLeft):
            it = QTableWidgetItem(text)
            it.setTextAlignment(align)
            return it

        name = str(item.get("name", ""))
        quality = str(item.get("quality", ""))

        self.setItem(row, 1, cell(name))
        self.setItem(row, 2, cell(quality))

        def price_item(v):
            try:
                return NumericItem(float(v))
            except Exception:
                return NumericItem(None)

        self.setItem(row, 3, price_item(item.get("site_price", "")))   # Site Fiyatı
        self.setItem(row, 4, price_item(item.get("market_price", ""))) # Pazar Fiyatı
        self.setItem(row, 5, price_item(None))                          # gizli

        # Kâr Oranı, Kâr Miktarı başlangıçta boş
        self.setItem(row, 6, NumericItem(None))
        self.setItem(row, 7, NumericItem(None))

        return row

    def update_image(self, row: int, pix):
        w = self.cellWidget(row, 0)
        if isinstance(w, QLabel):
            if pix is None:
                w.setText("Görsel yok")
            else:
                scaled = pix.scaled(w.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                w.setPixmap(scaled)
                w.setText("")

    def compute_profits(self):
        rows = self.rowCount()
        for r in range(rows):
            site_item = self.item(r, 3)
            market_item = self.item(r, 4)
            if not site_item or not market_item:
                continue
            try:
                site = site_item.value() if isinstance(site_item, NumericItem) else float(site_item.text() or "nan")
                market = market_item.value() if isinstance(market_item, NumericItem) else float(market_item.text() or "nan")
                if site != site or market != market:  # NaN
                    raise ValueError
                profit = market - site
                ratio = (profit / site * 100.0) if site > 0 else 0.0
            except Exception:
                profit, ratio = None, None

            pr_it = NumericItem(profit)
            rt_it = NumericItem(ratio)

            if profit is not None:
                pr_it.setForeground(QColor(60, 190, 90) if profit > 0 else QColor(220, 80, 80) if profit < 0 else QColor(220, 220, 220))
            if ratio is not None:
                rt_it.setForeground(QColor(60, 190, 90) if ratio > 0 else QColor(220, 80, 80) if ratio < 0 else QColor(220, 220, 220))

            self.setItem(r, 7, pr_it)  # Kâr Miktarı
            self.setItem(r, 6, rt_it)  # Kâr Oranı (%)


# -------------------- Fiyat çekme (yalnız pazar fiyatı) --------------------
class PriceFetchWorker(QObject):
    # key (mh), market_low, median
    progress = Signal(object, float, float)
    finished = Signal()

    def __init__(self, items: list[dict], currency: int, max_workers: int, rps: float, parent=None):
        super().__init__(parent)
        self.bucket = TokenBucket(rate=max(0.4, float(rps)), burst=3)
        self._mini_delay_overview = (0.15, 0.25)
        self._mini_delay_listing  = (0.20, 0.30)
        self._mini_delay_hist     = (0.20, 0.30)

        self.items = items
        self.currency = currency
        self.max_workers = max_workers
        self._stop = False

        self._nameid_cache: dict[str, str] = {}
        self._priceoverview_cache: dict[str, tuple[float|None, float|None]] = {}
        self._hist_lso_cache: dict[str, float|None] = {}

        self.session = make_session()

    def stop(self):
        self._stop = True

    def _should_stop(self):
        return self._stop

    def _fetch_one(self, idx: int, item: dict):
        mh = build_market_hash_name(item)
        if self._should_stop():
            return (mh, 0.0, 0.0)

        lp = mp = None
        lso = None

        # --- priceoverview ---
        try:
            if not self.bucket.acquire(1.0, self._should_stop):
                return (mh, 0.0, 0.0)
            time.sleep(random.uniform(*self._mini_delay_overview))

            url = ("https://steamcommunity.com/market/priceoverview/"
                   f"?country=TR&language=turkish&currency={self.currency}&appid=730"
                   f"&market_hash_name={quote_plus(mh)}")
            r = self.session.get(url, timeout=12)
            if r.ok:
                data = r.json()
                if not data.get("success", True):
                    time.sleep(0.5)
                    if not self.bucket.acquire(1.0, self._should_stop):
                        return (mh, 0.0, 0.0)
                    time.sleep(random.uniform(*self._mini_delay_overview))
                    r2 = self.session.get(url, timeout=12)
                    if r2.ok:
                        data = r2.json()
                if data.get("success", True):
                    lp = parse_money_to_float(data.get("lowest_price"))
                    mp = parse_money_to_float(data.get("median_price"))
        except Exception:
            pass
        if self._should_stop():
            return (mh, float(lp or 0.0), float(mp or 0.0))

        # --- fallback: histogram.lowest_sell_order ---
        if lp is None or lp == 0.0:
            nameid = self._nameid_cache.get(mh)
            if not nameid:
                try:
                    if not self.bucket.acquire(1.0, self._should_stop):
                        return (mh, float(lp or 0.0), float(mp or 0.0))
                    time.sleep(random.uniform(*self._mini_delay_listing))

                    url = f"https://steamcommunity.com/market/listings/730/{quote_plus(mh)}"
                    r = self.session.get(url, timeout=12)
                    if r.ok:
                        m = re.search(r"Market_LoadOrderSpread\\?\\?\\(\\?\\s*(\\d+)\\s*\\)", r.text)
                        if not m:
                            m = re.search(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)", r.text)
                        if m:
                            nameid = m.group(1)
                            self._nameid_cache[mh] = nameid
                except Exception:
                    nameid = None

            if nameid:
                try:
                    if not self.bucket.acquire(1.0, self._should_stop):
                        return (mh, float(lp or 0.0), float(mp or 0.0))
                    time.sleep(random.uniform(*self._mini_delay_hist))

                    url = ("https://steamcommunity.com/market/itemordershistogram"
                           f"?country=TR&language=turkish&currency={self.currency}"
                           f"&item_nameid={nameid}&two_factor=0&norender=1")
                    r = self.session.get(url, timeout=12)
                    if r.ok:
                        data = r.json()
                        y = data.get("lowest_sell_order")
                        try:
                            lso = int(y) / 100.0 if y not in (None, "") else None
                        except Exception:
                            lso = parse_money_to_float(y)
                        self._hist_lso_cache[nameid] = lso
                except Exception:
                    pass

        market_low = float(lp if lp not in (None, 0) else (lso or 0.0))
        median = float(mp or 0.0)

        try:
            print(f"[PRICE] {mh} -> market_low={market_low:.2f}")
        except Exception:
            pass

        return (mh, market_low, median)

    @Slot()
    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=max(1, int(self.max_workers))) as ex:
                futures = [ex.submit(self._fetch_one, idx, it) for idx, it in enumerate(self.items)]
                for fut in as_completed(futures):
                    if self._should_stop():
                        break
                    try:
                        key, market_low, median = fut.result()
                        self.progress.emit(key, market_low, median)
                    except Exception as e:
                        print("Fetch worker error:", e)
        finally:
            self.finished.emit()


# -------------------- Ana pencere --------------------
class MainWindow(QWidget):

    # --- Tema yardımcıları: SINIF METODLARI (init DIŞINDA!) ---
    def _palette_for_mode(self, mode: str) -> QPalette:
        # Her mod için mutlaka QPalette döndür!
        if mode == "light":
            return light_palette()
        elif mode == "grad_light":
            return light_palette()
        elif mode == "asimov":
            pal = QPalette()
            pal.setColor(QPalette.Window, QColor("#eeeeee"))
            pal.setColor(QPalette.Base, QColor("#ffffff"))
            pal.setColor(QPalette.AlternateBase, QColor("#f6f6f6"))
            pal.setColor(QPalette.WindowText, QColor("#000000"))
            pal.setColor(QPalette.Text, QColor("#000000"))
            pal.setColor(QPalette.ButtonText, QColor("#000000"))
            pal.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
            pal.setColor(QPalette.ToolTipText, QColor("#000000"))
            pal.setColor(QPalette.Button, QColor("#ffffff"))
            pal.setColor(QPalette.Highlight, QColor(ASIIMOV_ACCENT))
            pal.setColor(QPalette.HighlightedText, Qt.white)
            pal.setColor(QPalette.Link, QColor(ASIIMOV_ACCENT))
            pal.setColor(QPalette.BrightText, QColor(ASIIMOV_ACCENT))
            return pal
        elif mode == "grad_dark":
            pal = QPalette()
            pal.setColor(QPalette.Window, QColor("#0b0b10"))
            pal.setColor(QPalette.Base, QColor("#12121a"))
            pal.setColor(QPalette.AlternateBase, QColor("#191926"))
            pal.setColor(QPalette.WindowText, QColor("#E6E6E9"))
            pal.setColor(QPalette.Text, QColor("#E6E6E9"))
            pal.setColor(QPalette.Button, QColor("#1b1b28"))
            pal.setColor(QPalette.ButtonText, QColor("#E6E6E9"))
            pal.setColor(QPalette.ToolTipBase, QColor("#1f1f2d"))
            pal.setColor(QPalette.ToolTipText, QColor("#E6E6E9"))
            pal.setColor(QPalette.Highlight, QColor("#8b5cf6"))
            pal.setColor(QPalette.HighlightedText, Qt.white)
            pal.setColor(QPalette.Link, QColor("#a855f7"))
            pal.setColor(QPalette.BrightText, QColor("#a855f7"))
            return pal
        elif mode == "dark":
            # <-- BUNU EKLE: dark açıkça dönsün
            return dark_palette()

        # Güvenli varsayılan: hiçbiri eşleşmezse dark
        return dark_palette()


    def set_ui_mode(self, mode: str):
        self.ui_mode = mode
        app = QApplication.instance()

        # Palet ve stylesheet'i güvenle al
        pal = self._palette_for_mode(mode)
        if pal is None:                     # Savunmacı programlama
            pal = dark_palette()

        css = self._stylesheet_for_mode(mode)
        if not isinstance(css, str):        # Boş/None gelirse en azından boş string
            css = ""

        app.setStyle("Fusion")
        app.setPalette(pal)
        app.setStyleSheet(css)


    def _stylesheet_for_mode(self, mode: str) -> str:
        if mode == "grad_dark":
            return """
            /* Arka plan: siyah → mor gradyan */
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #0b0b10, stop:0.55 #141425, stop:1 #2a0a3a);
                color: #E6E6E9;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11pt;
            }

            /* Toolbar: yarı saydam koyu + mor alt çizgi */
            QToolBar {
                background: rgba(20,20,37,0.85);
                border: none;
                border-bottom: 2px solid #8b5cf6;
                padding: 6px;
            }

            /* Butonlar: koyu zemin + mor vurgu */
            QPushButton {
                background: #1b1b28;
                color: #EDEDF2;
                border: 1px solid #8b5cf6;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #26263a;
                border-color: #a78bfa;
            }
            QPushButton:pressed {
                background: #1a1630;
                border-color: #7c3aed;
            }
            QPushButton:disabled {
                color: #9a9aaa;
                border-color: #3a3a4a;
                background: #151520;
            }

            /* Giriş alanları */
            QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit {
                background: #12121a;
                color: #E6E6E9;
                border: 1px solid #3e3e5a;
                border-radius: 6px;
                padding: 4px 8px;
                selection-background-color: #8b5cf6;
                selection-color: white;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
                border: 1px solid #8b5cf6;
                box-shadow: 0 0 0 2px rgba(139,92,246,0.25);
            }

            /* Tablo başlıkları: mor şerit */
            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                            stop:0 #5221b5, stop:1 #8b5cf6);
                color: white;
                border: none;
                border-bottom: 1px solid #3a2a5a;
                padding: 6px 8px;
                font-weight: 600;
            }

            /* Tablo gövdesi */
            QTableWidget {
                background: #12121a;
                alternate-background-color: #191926;
                gridline-color: #2a2a3f;
                selection-background-color: #8b5cf6;
                selection-color: white;
                outline: none;
            }
            QTableWidget::item {
                padding: 4px 6px;
                border-bottom: 1px solid #1f1f2f;
            }
            QTableWidget::item:hover {
                background: #1a1630;
            }
            QTableWidget::item:selected {
                background: #8b5cf6;
                color: white;
                font-weight: 600;
            }
            QTableCornerButton::section {
                background: #2a0a3a;
                border: none;
            }

            /* Splitter: ince mor çizgi */
            QSplitter::handle {
                background: #3d2a5f;
                width: 3px;
            }

            /* ToolTip */
            QToolTip {
                background: #1f1f2d;
                color: #E6E6E9;
                border: 1px solid #8b5cf6;
                padding: 4px 6px;
                border-radius: 6px;
            }

            /* Tema butonu ve menü */
            QToolButton {
                background: #1b1b28;
                color: #EDEDF2;
                border: 1px solid #8b5cf6;
                border-radius: 8px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QToolButton:hover {
                background: #26263a;
                border-color: #a78bfa;
            }
            QMenu {
                background: #151523;
                color: #EDEDF2;
                border: 1px solid #8b5cf6;
            }
            QMenu::item:selected {
                background: #8b5cf6;
                color: white;
            }
            """

        if mode == "grad_light":
            return """
            QWidget {
                background: qlineargradient(x1:0,y1:0, x2:0,y2:1,
                            stop:0 #f5f7fa, stop:1 #eaeef2);
                color: #202124;
            }
            QToolBar { background: #c5c5c5; border-bottom: 1px solid #d0d7de; }
            QTableWidget::item:selected { background: #1769E0; color: white; }
            """
        if mode == "asimov":
            return f"""
            /* GENEL */
            QWidget {{
                background: #c5c5c5;
                color: #000000;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11pt;
            }}

            /* TOOLBAR */
            QToolBar {{
                background: #c5c5c5;
                border-bottom: 3px solid {ASIIMOV_ACCENT};
                padding: 6px;
            }}

            /* BUTONLAR */
            QPushButton {{
                background: {ASIIMOV_ACCENT};
                color: #c5c5c5;
                border: 2px solid #000000;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #ff8520;
                color: #c5c5c5;
                border-color: #000000;
            }}
            QPushButton:pressed {{
                background: #c55f12;
                color: #c5c5c5;
            }}
            QPushButton:disabled {{
                background: #cccccc;
                color: #666666;
                border: 1px solid #999999;
            }}

            /* METİN ALANLARI */
            QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit {{
                background: #c5c5c5;
                color: #000000;
                border: 2px solid {ASIIMOV_ACCENT};
                border-radius: 5px;
                padding: 4px 6px;
                selection-background-color: {ASIIMOV_ACCENT};
                selection-color: #c5c5c5;
            }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{
                border: 2px solid #000000;
            }}

            /* TABLO BAŞLIKLARI */
            QHeaderView::section {{
                background: {ASIIMOV_ACCENT};
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #c5c5c5;
                padding: 6px;
            }}

            /* TABLO GENEL */
            QTableWidget {{
                gridline-color: #fe6903;
                selection-background-color: {ASIIMOV_ACCENT};
                selection-color: #ffffff;
                alternate-background-color: #f5f5f5;
            }}

            /* TABLO HÜCRELERİ */
            QTableWidget::item {{
                border-bottom: 1px solid #cccccc;
                padding: 4px;
            }}
            QTableWidget::item:selected {{
                background: {ASIIMOV_ACCENT};
                color: #ffffff;
                font-weight: 600;
            }}
            QTableCornerButton::section {{
                background: {ASIIMOV_ACCENT};
                border: none;
            }}

            /* SPLITTER */
            QSplitter::handle {{
                background: {ASIIMOV_ACCENT};
                width: 3px;
            }}

            /* TOOLTIP */
            QToolTip {{
                background: #ffffff;
                color: #000000;
                border: 2px solid {ASIIMOV_ACCENT};
                padding: 4px;
                border-radius: 6px;
            }}

            /* THEME MENU BUTTON */
            QToolButton {{
                background: #ffffff;
                color: #000000;
                border: 2px solid {ASIIMOV_ACCENT};
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: 600;
            }}
            QToolButton:hover {{
                background: {ASIIMOV_ACCENT};
                color: #ffffff;
            }}

            /* MENU */
            QMenu {{
                background: #ffffff;
                color: #000000;
                border: 2px solid {ASIIMOV_ACCENT};
            }}
            QMenu::item:selected {{
                background: {ASIIMOV_ACCENT};
                color: #ffffff;
            }}
            """


        if mode == "light":
            return """
            QToolBar { background: #ffffff; border-bottom: 1px solid #d0d7de; }
            QTableWidget::item:selected { background: #1769E0; color: white; }
            """
        # dark
        return """
        QToolBar { background: #1b1d20; border: none; }
        QTableWidget::item:selected { background: #4c6ef5; color: white; }
        """

    def set_ui_mode(self, mode: str):
        self.ui_mode = mode
        app = QApplication.instance()
        app.setStyle("Fusion")
        app.setPalette(self._palette_for_mode(mode))
        app.setStyleSheet(self._stylesheet_for_mode(mode))

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SkinMarket-Analyzer")
        self.setWindowIcon(QIcon())
        self.resize(1350, 780)

        self.image_loader = ImageLoader(self)
        self.image_loader.image_ready.connect(self.on_image_ready)

        # Toolbar
        self.toolbar = QToolBar()
        act_open = QAction("JSON Aç", self)
        act_open.triggered.connect(self.open_json_file)
        act_save = QAction("JSON Kaydet", self)
        act_save.triggered.connect(self.save_json_file)
        self.toolbar.addAction(act_open)
        self.toolbar.addAction(act_save)

        # --- Tema butonu (açılır menü) ---
        self.btn_theme = QToolButton(self)
        self.btn_theme.setText("Tema")
        self.btn_theme.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)

        def _add_mode(label, key):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, k=key: self.set_ui_mode(k))
            menu.addAction(act)

        _add_mode(UI_MODES["dark"], "dark")
        _add_mode(UI_MODES["light"], "light")
        _add_mode(UI_MODES["grad_dark"], "grad_dark")
        _add_mode(UI_MODES["asimov"], "asimov")

        self.btn_theme.setMenu(menu)
        self.toolbar.addWidget(self.btn_theme)

        # Sol panel
        self.json_edit = QTextEdit()
        self.json_edit.setPlaceholderText("Buraya item JSON'unu yapıştır…")
        self.json_edit.setText(PLACEHOLDER_JSON)
        self.json_edit.setMinimumWidth(360)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Tabloyu filtrele (isim/kalite)…")
        self.filter_edit.textChanged.connect(self.apply_filter)

        self.btn_import = QPushButton("İçe Aktar")
        self.btn_import.clicked.connect(self.import_json)

        self.btn_run = QPushButton("Kârı Hesapla")
        self.btn_run.clicked.connect(self.run_compute)

        self.btn_fetch = QPushButton("Fiyatları Çek (Steam)")
        self.btn_fetch.clicked.connect(self.fetch_prices)

        self.btn_stop = QPushButton("Durdur")
        self.btn_stop.clicked.connect(self.stop_fetch)
        self.btn_stop.setEnabled(False)

        # Hız/Worker ayarları
        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 8)
        self.spin_workers.setValue(4)
        self.spin_rps = QDoubleSpinBox()
        self.spin_rps.setRange(0.5, 3.0)
        self.spin_rps.setSingleStep(0.1)
        self.spin_rps.setValue(1.8)
        form = QFormLayout()
        form.addRow("Worker (1-8):", self.spin_workers)
        form.addRow("İstek/sn (0.5–3.0):", self.spin_rps)

        left_buttons = QHBoxLayout()
        left_buttons.addWidget(self.btn_import)
        left_buttons.addWidget(self.btn_run)
        left_buttons.addWidget(self.btn_fetch)
        left_buttons.addWidget(self.btn_stop)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.toolbar)
        left_layout.addWidget(self.json_edit, 1)
        left_layout.addWidget(self.filter_edit)
        left_layout.addLayout(form)
        left_layout.addLayout(left_buttons)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)

        # Tablo
        self.table = PriceTable()
        self.table_frame = QFrame()
        self.table_frame.setLayout(QVBoxLayout())
        self.table_frame.layout().addWidget(self.table)

        # Çift tık → Pricempire
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)

        # Sağ tık menüsü
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(self.table_frame)
        splitter.setSizes([460, 900])

        root = QHBoxLayout(self)
        root.addWidget(splitter)
        self.setLayout(root)

        self.current_items = []
        self._row_by_key = {}   # mh -> row
        self._thread = None
        self._worker = None

    # -------- Yardımcılar --------
    def _set_numeric_cell(self, row: int, col: int, value: float | None):
        self.table.setItem(row, col, NumericItem(value if value is not None else None))

    def _on_table_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if row < 0:
            return
        self._open_pricempire_for_row(row)

    # -------- Çekme akışı --------
    def _open_pricempire_for_row(self, row: int):
        if row < 0:
            return
        raw_name = (self.table.item(row, 1).text() if self.table.item(row, 1) else "")
        quality = (self.table.item(row, 2).text() if self.table.item(row, 2) else "")
        stattrak_hint = None
        link_hint = None
        base_name = raw_name

        # Sticker engeli
        if "sticker" in raw_name.lower():
            QMessageBox.information(
                self, "Bilgi", "Sticker öğeleri için Pricempire linki oluşturulmaz."
            )
            return

        try:
            raw = self.current_items[row].get("_raw") if row < len(self.current_items) else None
            if isinstance(raw, dict):
                if "stattrak" in raw:
                    stattrak_hint = bool(raw.get("stattrak"))
                link_hint = raw.get("link")
        except Exception:
            pass

        if link_hint:
            pe = pricempire_canonicalize(link_hint) if 'pricempire_canonicalize' in globals() else link_hint
            if pe:
                webbrowser.open(pe)
                return

        if quality and raw_name.endswith(f" ({quality})"):
            base_name = raw_name[:-(len(quality)+3)].rstrip()

        url = build_pricempire_url(base_name, quality, stattrak_hint)
        if not url:
            QMessageBox.information(self, "Bilgi", "Bu item için Pricempire linki üretilemedi.")
            return
        webbrowser.open(url)

    def _on_table_context_menu(self, pos: QPoint):
        index = self.table.indexAt(pos)
        row = index.row()
        if row < 0:
            return
        menu = QMenu(self)
        act_open = QAction("Pricempire linkine git", self)
        act_copy_skin = QAction("Skin ismini kopyala", self)
        act_copy_full = QAction("Kopyala", self)
        menu.addAction(act_open)
        menu.addSeparator()
        menu.addAction(act_copy_skin)
        menu.addAction(act_copy_full)
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if not action:
            return
        if action is act_open:
            self._open_pricempire_for_row(row)
            return
        raw_name = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        quality = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
        base_name = raw_name
        if quality and raw_name.endswith(f" ({quality})"):
            base_name = raw_name[:-(len(quality)+3)].rstrip()

        cb = QApplication.clipboard()
        if action == act_copy_skin:
            cb.setText(base_name)
        elif action == act_copy_full:
            cb.setText(f"{base_name} ({quality})" if quality else base_name)

    @Slot()
    def fetch_prices(self):
        if not self.current_items:
            QMessageBox.information(self, "Bilgi", "Önce JSON'u içe aktar.")
            return

        # Sıralamayı kapat
        self._was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)

        # Butonlar / ayar
        self.btn_fetch.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_import.setEnabled(False)
        self.btn_stop.setEnabled(True)

        workers = int(self.spin_workers.value())
        rps = float(self.spin_rps.value())

        # QThread + Worker
        self._thread = QThread(self)
        self._worker = PriceFetchWorker(self.current_items, currency=1, max_workers=workers, rps=rps)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_fetch_progress)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_fetch_finished)

        self._thread.start()

    @Slot()
    def stop_fetch(self):
        if self._worker:
            self._worker.stop()
        self.btn_stop.setEnabled(False)

    @Slot(object, float, float)
    def _on_fetch_progress(self, key, market_low, median_price):
        row = self._row_by_key.get(key)
        if row is None:
            return
        # 4 = Pazar Fiyatı
        self._set_numeric_cell(row, 4, market_low)
        # Median tooltip
        cell = self.table.item(row, 4)
        if cell and median_price and median_price > 0:
            cell.setToolTip(f"Median: {median_price:.2f}")
        self.table.viewport().update()

    @Slot()
    def _on_fetch_finished(self):
        self.table.compute_profits()
        self.table.setSortingEnabled(getattr(self, "_was_sorting", True))
        self.btn_fetch.setEnabled(True)
        self.btn_run.setEnabled(True)
        self.btn_import.setEnabled(True)
        self.btn_stop.setEnabled(False)
        QMessageBox.information(self, "Bitti", "Steam fiyatları çekildi ve tabloya işlendi.")

    # -------- JSON uyarlayıcıları --------
    def _normalize_items(self, raw):
        out = []
        for it in raw:
            img = it.get("image_url") or it.get("icon_url") or it.get("image") or ""
            try:
                img = unquote(img)
            except Exception:
                pass
            out.append({
                "name": it.get("name", ""),
                "quality": it.get("quality", ""),
                "image_url": img,
                "site_price": it.get("sell_price", ""),
                "market_price": it.get("market_price", ""),
                "_raw": {
                    "id": it.get("id"),
                    "color": it.get("color"),
                    "stattrak": it.get("stattrak"),
                    "type": it.get("type"),
                    "link": it.get("link") or it.get("url"),
                }
            })
        return out

    @Slot()
    def import_json(self):
        txt = self.json_edit.toPlainText().strip()
        if not txt:
            QMessageBox.warning(self, "Uyarı", "JSON alanı boş!")
            return
        try:
            data = json.loads(txt)
            if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                items_raw = data["items"]
            elif isinstance(data, list):
                items_raw = data
            else:
                raise ValueError("Beklenen format: liste veya {'items': [...]}")

            if len(items_raw) > MAX_ITEMS:
                QMessageBox.information(self, "Bilgi", f"{len(items_raw)} adet geldi. İlk {MAX_ITEMS} tanesi yüklenecek.")
                items_raw = items_raw[:MAX_ITEMS]

            items = self._normalize_items(items_raw)
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"JSON okunamadı/uyarlanamadı:\n{e}")
            return

        self.current_items = items
        self.populate_table(items)

    def populate_table(self, items):
        self.table.setSortingEnabled(False)
        self.table.clear_rows()
        self._row_by_key.clear()

        for it in items:
            row = self.table.add_row(it)
            key = build_market_hash_name(it) or it.get("name", "")
            self._row_by_key[key] = row
            img_url = it.get("image_url", "")
            self.image_loader.fetch(row, img_url)

        self.table.setSortingEnabled(True)

    @Slot(int, object)
    def on_image_ready(self, row, pixmap):
        self.table.update_image(row, pixmap)

    @Slot()
    def run_compute(self):
        self.table.compute_profits()
        QMessageBox.information(self, "Tamam", "Kâr/Oran güncellendi.")

    def apply_filter(self, text):
        text = text.lower().strip()
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").lower()
            quality = (self.table.item(r, 2).text() if self.table.item(r, 2) else "").lower()
            self.table.setRowHidden(r, not (text in name or text in quality or text == ""))

    def open_json_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "JSON Dosyası Aç", "", "JSON (*.json);;Tümü (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
            self.json_edit.setText(txt)
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Dosya açılamadı:\n{e}")

    def save_json_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "JSON Olarak Kaydet", "items.json", "JSON (*.json)")
        if not path:
            return
        try:
            txt = self.json_edit.toPlainText()
            json.loads(txt)
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
            QMessageBox.information(self, "Kaydedildi", f"JSON kaydedildi:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Kaydedilemedi:\n{e}")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # isteğe bağlı; set_ui_mode zaten paleti basıyor
    w = MainWindow()
    w.set_ui_mode("dark")   # <-- başlangıç modu
    w.show()
    sys.exit(app.exec())



if __name__ == "__main__":
    main()

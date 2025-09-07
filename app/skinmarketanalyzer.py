# ui_cs2_profiler.py
# PySide6 arayüzü – yalnızca Steam'den fiyat çeker (priceoverview + fallback histogram)
# Özellikler:
#  - Worker sayısı ve istek hızı (req/sn) ayarlanabilir
#  - "Durdur" butonu ile çekimi kes
#  - Sipariş fiyatı (highest buy) KALDIRILDI (kolon da gizli)
#  - Satıra çift tıklayınca Pricempire sayfasını açar
#  - Tüm sayısal sütunlar doğru sıralanır (NumericItem)
# pip install PySide6 requests
from typing import Callable, Optional
import re, sys, json, time, random, threading, webbrowser
import requests
from html import unescape
from urllib.parse import quote_plus, unquote

from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from PySide6.QtCore import (
    Qt, QSize, QUrl, QObject, Signal, Slot, QBuffer, QByteArray, QThread
)
from PySide6.QtGui import (
    QPalette, QColor, QIcon, QAction, QPixmap, QImageReader
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QFileDialog,
    QHeaderView, QToolBar, QMessageBox, QLineEdit, QFrame, QAbstractItemView,
    QSpinBox, QDoubleSpinBox, QFormLayout
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


# -------------------- Genel sabitler --------------------
COLUMNS = [
    "Görsel", "İsim", "Kalite", "Site Fiyatı", "Pazar Fiyatı",
    "Sipariş Fiyatı", "Kâr Oranı (%)", "Kâr Miktarı"
]

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

def slugify(s: str) -> str:
    s = s.lower().replace("™", "")
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
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

def build_pricempire_url(name: str, quality: str = "", stattrak_hint: Optional[bool] = None) -> Optional[str]:
    info = parse_item_name(name)
    weapon, skin, wear, stat = info["weapon"], info["skin"], info["wear"], info["stat"]
    if not wear and quality in WEARS:
        wear = quality
    if stattrak_hint is True:
        stat = True
    cat = "glove" if ("glove" in weapon.lower() or "gloves" in weapon.lower()) else "skin"
    item_slug = slugify(f"{weapon} {skin}").replace("--", "-")
    wear_slug = WEAR_MAP.get(wear or "", None)
    if not item_slug or not wear_slug:
        return None
    wear_part = f"stattrak-{wear_slug}" if stat else wear_slug
    return f"https://pricempire.com/cs2-items/{cat}/{item_slug}/{wear_part}"


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
        name = (self.table.item(row, 1).text() if self.table.item(row, 1) else "")
        quality = (self.table.item(row, 2).text() if self.table.item(row, 2) else "")

        # stattrak ipucunu JSON’dan da yakala
        stattrak_hint = None
        try:
            raw = self.current_items[row].get("_raw") if row < len(self.current_items) else None
            if isinstance(raw, dict) and "stattrak" in raw:
                stattrak_hint = bool(raw.get("stattrak"))
        except Exception:
            pass

        url = build_pricempire_url(name, quality, stattrak_hint)
        if not url:
            QMessageBox.information(self, "Bilgi", "Bu item için Pricempire linki üretilemedi.")
            return
        webbrowser.open(url)

    # -------- Çekme akışı --------
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

            if len(items_raw) > 150:
                QMessageBox.information(self, "Bilgi", f"{len(items_raw)} adet geldi. İlk 150 tanesi yüklenecek.")
                items_raw = items_raw[:150]

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
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

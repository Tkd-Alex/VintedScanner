"""Parts Hunt: classify loose DDR4 SO-DIMM modules and SSDs from a listing title.

Pure functions, no I/O. The targets are the parts that fit a Dell OptiPlex 7090
Micro (Setup and Specifications, "Storage" and "Memory" pages on dell.com):

* memory: two DDR4 SO-DIMM slots, non-ECC;
* storage: M.2 slot SSD-0 takes a 2230 or 2280 PCIe NVMe SSD (2230 listed up
  to 512 GB), slot SSD-1 a 2280 PCIe NVMe SSD (up to 2 TB; Gen4 drives run at
  Gen3 speed), plus one 2.5-inch SATA bay (not on discrete-graphics units, and
  not together with two M.2 SSDs). M.2 SATA drives are not wanted.

Catalogue results carry only the title, so every rule works on short,
multilingual titles (Italian, French, Spanish, German, Dutch, English).
A title that does not clearly name a wanted part is rejected; ambiguities that
do not change the price target (module layout, SSD interface) become notes.
"""
from __future__ import annotations

import re
from decimal import Decimal

# Price caps in EUR on the item price (buyer fee and shipping excluded).
DEFAULT_CAPS = {
    "ram_8": 30,       # one 8 GB DDR4 SO-DIMM
    "ram_16": 50,      # one 16 GB DDR4 SO-DIMM
    "ram_2x16": 80,    # 2x16 GB kit (or 32 GB in total)
    "nvme_500": 40,    # 500/512 GB NVMe, only when really cheap
    "nvme_1tb": 110,
    "nvme_2tb": 190,
    "sata_1tb": 85,    # 2.5-inch SATA
    "sata_2tb": 150,
}
DEFAULT_DISCOUNT_MARGIN = Decimal("0.10")
CAP_LABELS = {
    "ram_8": "1×8 GB", "ram_16": "1×16 GB", "ram_2x16": "2×16 GB",
    "nvme_500": "NVMe 500 GB", "nvme_1tb": "NVMe 1 TB", "nvme_2tb": "NVMe 2 TB",
    "sata_1tb": "SATA 1 TB", "sata_2tb": "SATA 2 TB",
}
RAM_CAP_KEYS = ("ram_8", "ram_16", "ram_2x16")
SSD_CAP_KEYS = ("nvme_500", "nvme_1tb", "nvme_2tb", "sata_1tb", "sata_2tb")

_I = re.IGNORECASE
GB = r"(?:gb|go|g|giga(?:byte)?s?)"

RAM_WORD_RE = re.compile(
    r"\b(?:ram|ddr\s*\d|pc[234]l?-?\d{4,5}|so[\s_-]?dimm|sodimm|dimm|memoria|memorie|mémoire|memoire|"
    r"barrettes?|arbeitsspeicher|geheugen|memory|module de mémoire)\b", _I
)
SSD_WORD_RE = re.compile(r"\b(?:ssd|nvme|m\.?2|solid[\s-]?state|sólido|solido)\b", _I)
DDR4_RE = re.compile(r"\bddr\s*4l?\b|\bpc4l?-?\d{4,5}|\bpc4\b", _I)
OTHER_DDR_RE = re.compile(r"\bddr\s*[1235]l?\b|\bddr\b(?!\s*\d)|\bpc[1235]l?-?\d{3,5}|\bpc[235]\b|\bsdram\b|\bpc133\b", _I)
# Only DDR4 SO-DIMMs run at these speeds (DDR3 tops out at 2133, DDR5 starts at 4800).
DDR4_SPEED_RE = re.compile(r"\b(?:2400|2666|2667|2933|3200)\s*(?:mhz|mt/?s)?\b|\b(?:2400|2666|3200)[a-z]{1,2}\b", _I)
SODIMM_RE = re.compile(
    r"so[\s_-]?dimm|sodimm|so-dinn|\b260\s*-?\s*pin|\b(?:portatile|portatili|notebook|laptop|portátil|portatil|"
    r"pc portable|ordinateur portable|portable|laptops|notebooks|mac\s?mini|imac|nuc|mini\s?pc)\b", _I
)
DESKTOP_RAM_RE = re.compile(
    r"\b(?:udimm|desktop|fisso|288\s*-?\s*pin|rgb|lpx|fury beast|trident|ripjaws|ballistix|"
    r"vengeance(?! .*so)|dominator|xpg spectrix)\b", _I
)
SERVER_RAM_RE = re.compile(r"\b(?:ecc|reg|registered|rdimm|lrdimm|server|serveur)\b", _I)
WHOLE_SYSTEM_RE = re.compile(
    r"\b(?:i[3579][\s-]?\d{3,5}[a-z]{0,2}|core\s*i[3579]|i[3579]\b|ryzen|celeron|pentium|athlon|"
    r"windows|win\s?1[01]|\d{2}(?:[.,]\d)?\s*(?:\"|''|”|pollici|zoll|inch|pouces)|"
    r"\d{2}[.,]\d\b|gtx|rtx|geforce|radeon|chromebook|ideapad|thinkpad\s+[a-z]?\d|latitude\s+\d|"
    r"elitebook\s+\d|probook|inspiron|vivobook|zenbook|aspire\s+\d|pavilion|macbook\s+(?:pro|air)\s+\d)\b", _I
)
# "RAM per ThinkPad", "SSD pour MacBook": a part offered for a system, not the system.
FOR_SYSTEM_RE = re.compile(r"\b(?:per|for|pour|para|für|compatibil[ei]|compatible|voor)\b", _I)
# Three or more modules/drives: "6x SK Hynix 8GB", "set di 3", "4 barrettes".
# Counts written as "2x16GB" are read by ram_layout, which rejects more than two.
LOT_RE = re.compile(
    r"\b(?:set\s+(?:di|de|of)?\s*[3-9]|[3-9]\s*(?:pz|pcs|pezzi|stück)\b|[3-9]\s*[x×](?!\s*\d)|"
    r"[3-9]\s+(?:ram|ssd|barrettes|moduli|banchi|modules|sticks))", _I
)
KIT_WORD_RE = re.compile(
    r"\b(?:kit|pair|paire|coppia|couple|set|dual|matched|2\s*(?:pz|pcs|pezzi|stück)|"
    r"(?:2|two|due|deux|dos|zwei)\s+(?:x\s*)?(?:ram|barrettes?|moduli|modules?|banchi|sticks?|memorie|sodimm|so-dimm))\b", _I
)
EACH_RE = re.compile(r"\b(?:chacune|chacun|each|ciascuna|ciascuno|cad(?:auna|\.)?|cada una|je)\b", _I)
MODULE_SIZES = {4, 8, 16, 32, 64}

SSD_REJECT_RE = re.compile(
    r"\b(?:estern[oa]|external|externe|extern|usb|portable ssd|ssd portable|ssd port[aá]til|port[aá]til ssd|"
    r"t[579]\s+(?:shield|evo)?|t[579]\b|x[69]\b|x10\b|extreme|my passport|shield|"
    r"case|box|enclosure|boitier|boîtier|gehäuse|gehause|carcasa|custodia|adattatore|adapter|adaptateur|"
    r"adaptador|dock|docking|msata|u\.2|apple|macbook|imac|xbox|"
    r"2242|22110|raspberry|argon|synology|qnap|sshd|ssd\s*\+\s*hdd)\b", _I
)
HDD_RE = re.compile(
    r"\b(?:hdd|rpm|giri|festplatte|hard\s?disk|hard\s?drive|disque dur|disco duro|disco rigido|"
    r"wd\s?red(?!\s*(?:sn|sa))|ironwolf|barracuda|3[.,]5\s*(?:\"|''|”|pollici|zoll|pouces|inch))\b", _I
)
SOLID_RE = re.compile(r"\b(?:ssd|nvme|solid|sólido|solido|ssd)\b", _I)
NVME_RE = re.compile(
    r"\b(?:nvme|nmve|pcie|pci-?e|gen\s?[345](?:x4)?|9[789]0\s*(?:evo|pro)|990|980|970|9100|"
    r"sn[5-8]\d{2,3}|pm9[0-9a-z]{2,3}|p[1-5](?:\s?plus)?|p3[1-9]0|p5[1-9]0|t[57]00|kc3000|nv[1-3]|"
    r"mp[56]00|a2000|a1000|s880e?|spatium|fury renegade|firecuda|legend\s?\d{3}|eq790|optimus|"
    r"bc[5-9]\d{2}|pc\d{3}\b|2550|2210|mp600|dissipatore|dissipateur|heatsink|kühlkörper|disipador)\b", _I
)
NVME_EXPLICIT_RE = re.compile(r"\b(?:nvme|nmve|pcie|pci-?e|pcle)\b", _I)
MULTI_DRIVE_RE = re.compile(
    r"(?<![.\d])\b[2-9]\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:tb|to|gb|go)\b|"
    r"(?<![.\d])\b(?:2|two|due|deux|dos|zwei)\s*(?:x|×)?\s*(?:ssd|nvme|wd|samsung|crucial|kingston)\b|"
    r"\b(?:set|kit)\s+(?:di|de|of)?\s*2\b", _I
)
M2_RE = re.compile(r"\bm\.?\s?2\b|\b2280\b|\b2230\b|\bngff\b", _I)
SATA_RE = re.compile(
    r"\b(?:sata|2[.,]5\s*(?:\"|''|”|pollici|zoll|pouces|inch)?|8[4-7]0\s*(?:evo|pro|qvo)?|qvo|"
    r"mx[1-5]00|bx[1-5]00|a400|sa[45][0-9]0|ultra\s?(?:ii|3d)|ssd plus|cs[39]00|a55)\b", _I
)
SIZE_2230_RE = re.compile(r"\b2230\b", _I)
GEN45_RE = re.compile(r"\b(?:gen\s?[45](?:x4)?|pcie\s?[45](?:\.0)?)\b", _I)
CAPACITY_TB_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:tb|to|t|tera(?:byte)?s?)\b", _I)
CAPACITY_GB_RE = re.compile(r"\b(\d{3,4})\s*(?:gb|go|g|giga(?:byte)?s?)\b", _I)


def _num(text: str) -> float:
    return float(text.replace(",", "."))


# -- RAM -----------------------------------------------------------------------
def ram_layout(title: str) -> tuple[int | None, int | None, list[str]] | None:
    """Return (module count, GB per module, notes) for a memory title, or None.

    A count of None means the layout is unknown; the size is then the stated
    capacity. Lots of more than two modules return None.
    """
    notes: list[str] = []
    # 2x16GB, 2 x 8 Go, 16GB x 2, 16*2, (8GB x 2), 8×1 GB.
    for match in re.finditer(
        rf"\b(\d{{1,2}})\s*({GB})?\s*[x×*]\s*(\d{{1,2}})\s*({GB})?\b", title, _I
    ):
        first, first_unit, second, second_unit = int(match.group(1)), match.group(2), int(match.group(3)), match.group(4)
        if second_unit and not first_unit:
            count, size = first, second
        elif first_unit and not second_unit:
            size, count = first, second
        elif first in MODULE_SIZES and second not in MODULE_SIZES:
            size, count = first, second
        else:
            count, size = first, second
        if size not in MODULE_SIZES or not 1 <= count <= 8:
            continue
        return (count, size, notes) if count <= 2 else None
    # 8GB + 8GB, 8+16 GB
    match = re.search(rf"\b(\d{{1,2}})\s*{GB}?\s*\+\s*(\d{{1,2}})\s*{GB}\b", title, _I)
    if match:
        first, second = int(match.group(1)), int(match.group(2))
        if first == second and first in MODULE_SIZES:
            return 2, first, notes
        return None

    sizes = [int(value) for value in re.findall(rf"\b(\d{{1,2}})\s*{GB}\b", title, _I)]
    sizes = [size for size in sizes if size in MODULE_SIZES]
    if not sizes:
        return None
    distinct = sorted(set(sizes))
    if len(distinct) == 2 and distinct[1] == 2 * distinct[0]:
        # "16GB (32GB)": two modules of the smaller size.
        return 2, distinct[0], notes
    if len(distinct) > 1:
        return None
    size = distinct[0]
    if KIT_WORD_RE.search(title):
        if EACH_RE.search(title):
            return 2, size, notes
        # "Kit 16 GB": the figure is the total of two modules.
        if size // 2 in MODULE_SIZES:
            notes.append("kit: the stated size is assumed to be the total of two modules")
            return 2, size // 2, notes
        return None
    return None, size, notes


def classify_ram(title: str, description: str = "") -> dict | None:
    """Return the wanted DDR4 SO-DIMM part named by ``title``, or None."""
    text = f"{title}\n{description}"
    if not RAM_WORD_RE.search(title) or SSD_WORD_RE.search(title):
        return None
    if WHOLE_SYSTEM_RE.search(title) and not FOR_SYSTEM_RE.search(title):
        return None
    if LOT_RE.search(title):
        return None
    if SERVER_RAM_RE.search(title):
        return None
    sodimm = bool(SODIMM_RE.search(text))
    if not sodimm:
        return None
    if DESKTOP_RAM_RE.search(title) and not re.search(r"so[\s_-]?dimm|sodimm", title, _I):
        return None
    if OTHER_DDR_RE.search(title) and not DDR4_RE.search(title):
        return None

    layout = ram_layout(title)
    if layout is None:
        return None
    count, size, notes = layout
    notes = list(notes)

    ddr4 = bool(DDR4_RE.search(text))
    if not ddr4 and DDR4_SPEED_RE.search(title):
        ddr4 = True
    if not ddr4:
        # 16 GB SO-DIMMs are practically always DDR4; 8 GB ones are often DDR3.
        if (size or 0) < 16:
            return None
        notes.append("DDR generation not stated: ask for DDR4")

    if count is None:
        if size == 8:
            key, part = "ram_8", "DDR4 SO-DIMM 8 GB"
            notes.append("one module assumed: check it is not 2×4 GB")
        elif size == 16:
            key, part = "ram_16", "DDR4 SO-DIMM 16 GB"
            notes.append("ask: one 16 GB module or 2×8 GB?")
        elif size == 32:
            key, part = "ram_2x16", "DDR4 SO-DIMM 32 GB"
            notes.append("ask: 2×16 GB kit or one 32 GB module (both fit)")
        else:
            return None
        total = size
    elif count == 1 and size in (8, 16, 32):
        key = {8: "ram_8", 16: "ram_16", 32: "ram_2x16"}[size]
        part = f"DDR4 SO-DIMM {size} GB (1×{size} GB)"
        total = size
    elif count == 2 and size == 16:
        key, part, total = "ram_2x16", "DDR4 SO-DIMM 32 GB (2×16 GB)", 32
    else:
        return None
    return {"kind": "ram", "cap_key": key, "part": part, "gb": total, "notes": notes}


# -- SSD -----------------------------------------------------------------------
def ssd_capacity_gb(title: str) -> float | None:
    match = CAPACITY_TB_RE.search(title)
    if match:
        value = _num(match.group(1))
        # "1,92 To" = 1920 GB; ignore "15 T" style typos.
        return value * 1000 if value <= 8 else None
    match = CAPACITY_GB_RE.search(title)
    return float(match.group(1)) if match else None


def _all_capacities(title: str) -> list[float]:
    values = [_num(m.group(1)) * 1000 for m in CAPACITY_TB_RE.finditer(title) if _num(m.group(1)) <= 8]
    values += [float(m.group(1)) for m in CAPACITY_GB_RE.finditer(title)]
    return values


def ssd_size_class(capacity_gb: float | None) -> str | None:
    if capacity_gb is None:
        return None
    if 450 <= capacity_gb <= 530:
        return "500"
    if 900 <= capacity_gb <= 1100:
        return "1tb"
    if capacity_gb >= 1800:
        return "2tb"
    return None


def classify_ssd(title: str, description: str = "") -> dict | None:
    """Return the wanted SSD named by ``title``, or None."""
    if not SOLID_RE.search(title) and not (M2_RE.search(title) and NVME_RE.search(title)):
        return None
    if HDD_RE.search(title) and not SOLID_RE.search(title):
        return None
    if re.search(r"\bhdd\b", title, _I):
        return None
    if SSD_REJECT_RE.search(title):
        return None
    if WHOLE_SYSTEM_RE.search(title) and not FOR_SYSTEM_RE.search(title):
        return None
    if re.search(rf"\b\d{{1,2}}\s*{GB}\s*(?:di\s+)?(?:ram|ddr)", title, _I):
        return None  # "16GB RAM ... 1TB SSD": a whole system
    if LOT_RE.search(title) or MULTI_DRIVE_RE.search(title):
        return None

    size = ssd_size_class(ssd_capacity_gb(title))
    if size is None:
        return None
    # "1tb ssd 256gb m2": two drives, usually a whole PC.
    if len({cls for cls in map(ssd_size_class, _all_capacities(title)) if cls} | {size}) > 1 or any(
        ssd_size_class(value) is None for value in _all_capacities(title)
    ):
        return None

    notes: list[str] = []
    nvme = bool(NVME_RE.search(title))
    m2 = bool(M2_RE.search(title))
    sata = bool(SATA_RE.search(title))
    if sata and nvme and not NVME_EXPLICIT_RE.search(title) and re.search(r"\bsata\b", title, _I):
        nvme = False  # "M.2 SATA ... 990 PRO": the stated interface wins over a model name
    if nvme:
        interface = "nvme"
    elif m2 and sata:
        return None  # M.2 SATA: not wanted
    elif m2:
        interface = "nvme"
        notes.append("interface not stated: check it is NVMe, not M.2 SATA")
    elif sata:
        interface = "sata"
    else:
        interface = "sata"
        notes.append("interface not stated: check it is a 2.5\" SATA SSD (or NVMe)")

    if SIZE_2230_RE.search(title):
        if interface != "nvme":
            return None
        notes.append("2230: fits only M.2 slot SSD-0 (Dell lists 2230 up to 512 GB)")
    if interface == "nvme" and GEN45_RE.search(title):
        notes.append("PCIe Gen4/5 drives run at Gen3 speed in the 7090 Micro")
    if interface == "sata":
        notes.append("2.5\" bay: needs the caddy and cable; absent on discrete-GPU units")
    if size == "500" and interface != "nvme":
        return None

    key = f"{interface}_{size}"
    label = {"500": "500 GB", "1tb": "1 TB", "2tb": "2 TB"}[size]
    capacity = ssd_capacity_gb(title)
    if size == "2tb" and capacity and capacity > 2100:
        label = f"{capacity / 1000:g} TB"
        notes.append("Dell lists drives only up to 2 TB; very cheap large drives are often fakes")
    part = f"{'NVMe M.2' if interface == 'nvme' else 'SATA 2.5″'} SSD {label}"
    gb = capacity if capacity else {"500": 500, "1tb": 1000, "2tb": 2000}[size]
    return {"kind": "ssd", "cap_key": key, "part": part, "gb": gb, "notes": notes}


# -- pricing -------------------------------------------------------------------
def merge_caps(configured: dict | None) -> dict[str, Decimal]:
    """Return DEFAULT_CAPS overridden by ``configured``; raise ValueError on bad input."""
    caps = {key: Decimal(str(value)) for key, value in DEFAULT_CAPS.items()}
    if configured is None:
        return caps
    if not isinstance(configured, dict):
        raise ValueError("parts_hunt_caps must be a {part: max_price_eur} dictionary")
    for key, value in configured.items():
        if key not in DEFAULT_CAPS:
            raise ValueError(f"parts_hunt_caps contains an unknown part: {key}")
        try:
            caps[key] = Decimal(str(value))
        except Exception as error:  # decimal.InvalidOperation and friends
            raise ValueError(f"parts_hunt_caps price for {key} must be numeric") from error
        if caps[key] <= 0:
            raise ValueError(f"parts_hunt_caps price for {key} must be greater than zero")
    return caps


def analyze(title: str, price: Decimal | None, caps: dict[str, Decimal],
            margin: Decimal = DEFAULT_DISCOUNT_MARGIN, description: str = "") -> tuple[dict | None, bool]:
    """Return (candidate or None, over_cap).

    ``over_cap`` is True for a wanted part priced above cap + margin: the caller
    skips it without remembering it, so a later price drop is still noticed.
    """
    if price is None:
        return None, False
    part = classify_ram(title, description) or classify_ssd(title, description)
    if part is None:
        return None, False
    cap = caps[part["cap_key"]]
    limit = cap * (1 + margin)
    if price > limit:
        return None, True
    over_pct = int(((price - cap) / cap * 100).to_integral_value(rounding="ROUND_CEILING")) if price > cap else 0
    candidate = dict(
        part,
        price=price,
        cap=cap,
        band="target" if price <= cap else "discount",
        over_pct=over_pct,
        eur_per_gb=(price / Decimal(str(part["gb"]))) if part["gb"] else None,
    )
    return candidate, False


def headline_tier(candidate: dict) -> str:
    """Tier text shown in the alert headline and stored in ``listings.tier``."""
    if candidate["band"] == "target":
        return f"TARGET — {candidate['part']}"
    # The percentage stays in the alert body so the dashboard can group by part.
    return f"ASK DISCOUNT — {candidate['part']}"


def checklist(candidate: dict) -> list[str]:
    if candidate["kind"] == "ram":
        return [
            "• DDR4 SO-DIMM (laptop size), non-ECC",
            "• number and size of the modules (photo of the labels)",
            "• tested and working; return accepted if faulty",
        ]
    return [
        "• exact model and form factor (2.5\" SATA, or M.2 2280/2230 NVMe)",
        "• SMART health and power-on hours (CrystalDiskInfo screenshot)",
        "• not an external/USB drive or a drive pulled from a Mac",
    ]

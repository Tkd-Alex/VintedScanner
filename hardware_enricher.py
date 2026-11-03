"""Local hardware analysis for VintedScanner Telegram alerts.

The module does not contact third-party AI services. It extracts claims from the
listing and enriches recognized CPU models from a small, editable local catalog.
Unknown facts stay unknown; listing claims are not treated as independently tested.
"""
from __future__ import annotations

import html
import json
import logging
import re
from html.parser import HTMLParser
from typing import Any


REQUEST_TIMEOUT_SECONDS = 15
MAX_DESCRIPTION_CHARS = 520
DEFAULT_RAM_8GB_UPGRADE_EUR = 50
DEFAULT_RAM_32GB_KIT_EUR = 130

# Relative CPU capability points on the 0-50 portion of the ranking. These are
# curated tiers for this use case, not benchmark results. Update when the local
# CPU catalog is extended or better model-specific data becomes available.
CPU_PERFORMANCE_POINTS: dict[str, int] = {
    "i56500t": 19, "i57500": 23, "i57500t": 22,
    "i58400t": 30, "i58500": 34, "i58500t": 32, "i58600t": 34,
    "i76700t": 24, "i77700t": 27, "i78700t": 39, "i78700": 43,
    "i59500": 37, "i59500t": 35, "i79700": 47, "i79700t": 44,
    "i510400": 39, "i510500": 42, "i510500t": 40,
    "i710700": 50, "i710700t": 48,
    "i511500": 45, "i511500t": 43, "i711700": 50, "i711700t": 50,
    "i512500": 48, "i512500t": 47, "i712700": 50, "i712700t": 50,
    "n100": 17,
    "ryzen3pro2200ge": 17, "ryzen5pro2400ge": 23,
    "ryzen5pro4650ge": 37, "ryzen7pro4750ge": 43,
    "ryzen5pro5650ge": 40, "ryzen7pro5750ge": 47,
    "ryzen56600h": 44, "ryzen76800h": 50,
}

# Keys are canonical, lowercase tokens. Specifications are reference data for
# commonly listed mini-PC CPUs; frequencies are intentionally omitted because
# seller listings often omit suffixes or misidentify the processor.
CPU_CATALOG: dict[str, dict[str, Any]] = {
    "i56500t": {"name": "Intel Core i5-6500T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
    "i57500": {"name": "Intel Core i5-7500", "cores": 4, "threads": 4, "tdp_w": 65, "gpu": "Intel HD Graphics 630"},
    "i57500t": {"name": "Intel Core i5-7500T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 630"},
    "i58400t": {"name": "Intel Core i5-8400T", "cores": 6, "threads": 6, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i58500": {"name": "Intel Core i5-8500", "cores": 6, "threads": 6, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i58500t": {"name": "Intel Core i5-8500T", "cores": 6, "threads": 6, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i58600t": {"name": "Intel Core i5-8600T", "cores": 6, "threads": 6, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i76700t": {"name": "Intel Core i7-6700T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
    "i78700": {"name": "Intel Core i7-8700", "cores": 6, "threads": 12, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i77700t": {"name": "Intel Core i7-7700T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel HD Graphics 630"},
    "i78700t": {"name": "Intel Core i7-8700T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i59500": {"name": "Intel Core i5-9500", "cores": 6, "threads": 6, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i59500t": {"name": "Intel Core i5-9500T", "cores": 6, "threads": 6, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i79700": {"name": "Intel Core i7-9700", "cores": 8, "threads": 8, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i79700t": {"name": "Intel Core i7-9700T", "cores": 8, "threads": 8, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i510400": {"name": "Intel Core i5-10400", "cores": 6, "threads": 12, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i510500": {"name": "Intel Core i5-10500", "cores": 6, "threads": 12, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i510500t": {"name": "Intel Core i5-10500T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i710700": {"name": "Intel Core i7-10700", "cores": 8, "threads": 16, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i710700t": {"name": "Intel Core i7-10700T", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i511500": {"name": "Intel Core i5-11500", "cores": 6, "threads": 12, "tdp_w": 65, "gpu": "Intel UHD Graphics 750"},
    "i511500t": {"name": "Intel Core i5-11500T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 750"},
    "i711700": {"name": "Intel Core i7-11700", "cores": 8, "threads": 16, "tdp_w": 65, "gpu": "Intel UHD Graphics 750"},
    "i711700t": {"name": "Intel Core i7-11700T", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Intel UHD Graphics 750"},
    "i512500": {"name": "Intel Core i5-12500", "cores": 6, "threads": 12, "tdp_w": 65, "gpu": "Intel UHD Graphics 770"},
    "i512500t": {"name": "Intel Core i5-12500T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "i712700": {"name": "Intel Core i7-12700", "cores": 12, "threads": 20, "tdp_w": 65, "gpu": "Intel UHD Graphics 770"},
    "i712700t": {"name": "Intel Core i7-12700T", "cores": 12, "threads": 20, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "n100": {"name": "Intel Processor N100", "cores": 4, "threads": 4, "tdp_w": 6, "gpu": "Intel UHD Graphics (Alder Lake-N)"},
    "ryzen3pro2200ge": {"name": "AMD Ryzen 3 PRO 2200GE", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro2400ge": {"name": "AMD Ryzen 5 PRO 2400GE", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Radeon Vega 11 Graphics"},
    "ryzen5pro4650ge": {"name": "AMD Ryzen 5 PRO 4650GE", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Radeon Vega 7 Graphics"},
    "ryzen7pro4750ge": {"name": "AMD Ryzen 7 PRO 4750GE", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro5650ge": {"name": "AMD Ryzen 5 PRO 5650GE", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Radeon Vega 7 Graphics"},
    "ryzen7pro5750ge": {"name": "AMD Ryzen 7 PRO 5750GE", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen56600h": {"name": "AMD Ryzen 5 6600H", "cores": 6, "threads": 12, "tdp_w": 45, "gpu": "AMD Radeon 660M"},
    "ryzen76800h": {"name": "AMD Ryzen 7 6800H", "cores": 8, "threads": 16, "tdp_w": 45, "gpu": "AMD Radeon 680M"},
}

# Parse longer / exact models first, so e.g. i5-7500T cannot be reduced to i5-7500.
INTEL_CPU_RE = re.compile(r"\b(?:intel\s*)?(?:core\s*)?(i[3579])\s*[- ]?\s*(\d{4,5})([a-z]{0,2})\b", re.I)
AMD_CPU_RE = re.compile(r"\bryzen\s*([3579])\s*(pro\s*)?(\d{4,5}[a-z]{0,2})\b", re.I)
N100_RE = re.compile(r"\b(?:intel\s+processor\s+)?n100\b", re.I)


def _canonical_cpu(text: str) -> tuple[str | None, dict[str, Any] | None]:
    n100 = N100_RE.search(text)
    if n100:
        return "Intel Processor N100", CPU_CATALOG["n100"]

    for match in INTEL_CPU_RE.finditer(text):
        key = (match.group(1) + match.group(2) + (match.group(3) or "")).lower()
        # A suffixless model is not the same SKU as its low-power T version.
        if key in CPU_CATALOG:
            return CPU_CATALOG[key]["name"], CPU_CATALOG[key]

    for match in AMD_CPU_RE.finditer(text):
        family = "ryzen" + match.group(1) + ("pro" if match.group(2) else "") + match.group(3)
        key = re.sub(r"[^a-z0-9]", "", family.lower())
        if key in CPU_CATALOG:
            return CPU_CATALOG[key]["name"], CPU_CATALOG[key]

    return None, None


class _ListingHTMLParser(HTMLParser):
    """Extract safe, generic metadata without adding BeautifulSoup as a dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self._script_type = ""
        self._script_chunks: list[str] = []
        self.json_ld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = (attr.get("property") or attr.get("name") or attr.get("itemprop")).lower()
            value = attr.get("content", "").strip()
            if key and value:
                self.meta[key] = value
        elif tag.lower() == "script":
            self._script_type = attr.get("type", "").lower()
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_type == "application/ld+json":
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            if self._script_type == "application/ld+json":
                chunk = "".join(self._script_chunks).strip()
                if chunk:
                    self.json_ld.append(chunk)
            self._script_type = ""
            self._script_chunks = []


def _walk_json_for_description(value: Any) -> list[str]:
    descriptions: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() == "description" and isinstance(child, str):
                cleaned = re.sub(r"\s+", " ", child).strip()
                if cleaned:
                    descriptions.append(cleaned)
            descriptions.extend(_walk_json_for_description(child))
    elif isinstance(value, list):
        for child in value:
            descriptions.extend(_walk_json_for_description(child))
    return descriptions


def fetch_listing_description(item_url: str, session: Any = None) -> str:
    """Try to obtain listing text from the item's page; failure is non-fatal.

    Uses one bounded, normal page request for a newly detected listing. It does
    not use proxies, rotate identities, or try to evade access controls.
    """
    if not item_url:
        return ""
    try:
        if session is None:
            import requests
            response = requests.get(item_url, timeout=REQUEST_TIMEOUT_SECONDS)
        else:
            response = session.get(item_url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        parser = _ListingHTMLParser()
        parser.feed(response.text[:2_000_000])

        candidates: list[str] = []
        for raw in parser.json_ld:
            try:
                candidates.extend(_walk_json_for_description(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        for key in ("description", "og:description", "twitter:description"):
            value = parser.meta.get(key)
            if value:
                candidates.append(value)

        # Avoid treating generic marketplace SEO copy as the seller's description.
        filtered = []
        for candidate in candidates:
            text = re.sub(r"\s+", " ", html.unescape(candidate)).strip()
            low = text.lower()
            if not text or low in {"vinted", ""}:
                continue
            if "compra e vendi" in low and len(text) < 220:
                continue
            if len(text) > 20 and text not in filtered:
                filtered.append(text)
        if not filtered:
            return ""
        return max(filtered, key=len)[:MAX_DESCRIPTION_CHARS]
    except Exception as error:  # detail enrichment must never stop the base notifier
        logging.info("Could not fetch listing description (%s): %s", item_url, error)
        return ""


def _find_ram_gb(text: str) -> int | None:
    # Prefer number-first phrasing: "16GB RAM". Do this before "RAM 512GB"
    # because in "16GB RAM 512GB NVMe" the latter sequence would misread SSD size as RAM.
    explicit_after = re.search(r"\b(\d{1,3})\s*gb\s*(?:ddr[345]\s*)?(?:ram|memory|di ram)\b", text, re.I)
    if explicit_after:
        return int(explicit_after.group(1))
    explicit = re.search(r"\b(?:ram|memory|memoria)\s*[:=]\s*(\d{1,3})\s*gb\b", text, re.I)
    if explicit:
        return int(explicit.group(1))
    explicit = re.search(r"\b(?:ram|memory|memoria)\s+(\d{1,3})\s*gb\b", text, re.I)
    if explicit:
        return int(explicit.group(1))

    # Common resale title pattern: "i5-8600T | 16GB | 256GB SSD". Treat as
    # RAM the first capacity that is not attached to an explicit storage label.
    storage_spans = [m.span() for m in re.finditer(
        r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\s*(?:nvme|ssd|hdd|sshd|sata|m\.2|hard\s?disk|hard\s?drive)",
        text, re.I
    )]
    capacity_matches = list(re.finditer(r"\b(\d{1,4})\s*gb\b", text, re.I))
    for match in capacity_matches:
        if not any(start <= match.start() < end for start, end in storage_spans):
            # If the capacity appears after a word like SSD/HDD, ignore it.
            before = text[max(0, match.start() - 14):match.start()].lower()
            if re.search(r"(?:ssd|hdd|sshd|nvme|storage|archiviazione)[\s:=]*$", before):
                continue
            return int(match.group(1))
    return None


def _find_storage(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    # Capacity first: 512 GB NVMe SSD, 500GB SSHD.
    pattern_a = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb)\s*(nvme(?:\s+ssd)?|ssd|hdd|sshd|sata\s+ssd|m\.2(?:\s+ssd)?)\b", re.I)
    for match in pattern_a.finditer(text):
        capacity = match.group(1).replace(",", ".")
        unit = match.group(2).upper()
        kind = re.sub(r"\s+", " ", match.group(3)).upper()
        found.append((match.start(), f"{capacity} {unit} {kind}"))
    # Reverse order: NVMe 512GB / SSD da 512 GB.
    pattern_b = re.compile(r"\b(nvme|ssd|hdd|sshd|sata\s+ssd)\s*(?:da|of)?\s*(\d+(?:[.,]\d+)?)\s*(gb|tb)\b", re.I)
    for match in pattern_b.finditer(text):
        capacity = match.group(2).replace(",", ".")
        found.append((match.start(), f"{capacity} {match.group(3).upper()} {match.group(1).upper()}"))
    # Remove duplicate readings of the same span/value.
    result: list[str] = []
    for _, value in sorted(found):
        if value not in result:
            result.append(value)
    return result[:4]


def _find_model(text: str) -> str | None:
    patterns = [
        (r"\bLenovo\s+ThinkCentre\s+M(?:\d{3,4}|[789]\d{1,2})[qpx]?\b", "Lenovo ThinkCentre"),
        (r"\bLenovo\s+M(?:\d{3,4}|[789]\d{1,2})[qpx]?\b", "Lenovo ThinkCentre"),
        (r"\bThinkCentre\s+M(?:\d{3,4}|[789]\d{1,2})[qpx]?\b", "Lenovo ThinkCentre"),
        (r"\b(?:HP\s+)?EliteDesk\s+\d{3,4}\s*(?:G\d\s*)?(?:Mini|DM|Desktop Mini)\b", "HP EliteDesk"),
        (r"\b(?:HP\s+)?ProDesk\s+\d{3,4}\s*(?:G\d\s*)?(?:Mini|DM|Desktop Mini)\b", "HP ProDesk"),
        (r"\bDell\s+OptiPlex\s+\d{4}\s+Micro\b", "Dell OptiPlex"),
        (r"\bOptiPlex\s+\d{4}\s+Micro\b", "Dell OptiPlex"),
        (r"\b(?:Geekom\s+)?A\d\b", "GEEKOM"),
    ]
    for pattern, _label in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _is_target_system(text: str) -> tuple[bool, str | None]:
    """Accept only the compact business-PC families requested by the user.

    A generic "mini PC", SFF/USFF chassis, or a GEEKOM model is not enough.
    The family and the matching small-form-factor marker must be explicit.
    """
    checks = [
        (r"\b(?:HP\s+)?EliteDesk\s+(?:(?:\d{3,4})(?:\s+G\d)?\s+)?(?:Mini|DM|Desktop\s+Mini)\b", "HP EliteDesk Mini"),
        (r"\b(?:Lenovo\s+)?ThinkCentre\s+(?:M(?:\d{3,4}|[789]\d{1,2})q|[^\n]{0,35}\bTiny)\b", "Lenovo ThinkCentre Tiny"),
        (r"\b(?:Dell\s+)?OptiPlex\s+\d{4}\s+Micro(?:\s+Form\s+Factor)?\b", "Dell OptiPlex Micro"),
    ]
    for pattern, family in checks:
        if re.search(pattern, text, re.I):
            return True, family
    return False, None


def _parse_price_eur(price: str | None) -> float | None:
    """Read a simple EUR amount from normalized Vinted price text."""
    if not price:
        return None
    value = str(price).strip().replace("€", " ")
    # Handle European thousands/decimal separators, e.g. 1.299,00 EUR.
    european = re.search(r"(?<!\d)(\d{1,3}(?:\.\d{3})+,\d{1,2})(?!\d)", value)
    if european:
        return float(european.group(1).replace(".", "").replace(",", "."))
    match = re.search(r"(?<!\d)(\d+(?:[.,]\d{1,2})?)(?!\d)", value)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _upgrade_estimates(
    ram_gb: int | None,
    price: str | None,
    ram_8gb_upgrade_eur: float,
    ram_32gb_kit_eur: float,
) -> dict[str, Any]:
    """Estimate outlay for 16/32 GB without assuming unknown RAM topology."""
    listing_price = _parse_price_eur(price)
    upgrade_16: float | None
    upgrade_32: float | None
    note = None

    if ram_gb is None:
        upgrade_16 = None
        upgrade_32 = None
        note = "RAM non indicata: costo upgrade da verificare"
    else:
        if ram_gb >= 16:
            upgrade_16 = 0.0
        elif ram_gb == 8:
            # Assumes the existing 8 GB is usable and a compatible slot is free.
            upgrade_16 = float(ram_8gb_upgrade_eur)
            note = "16 GB stimati: ipotesi di aggiunta 1×8 GB compatibile"
        else:
            upgrade_16 = None
            note = "RAM attuale sotto 8/16 GB: costo per 16 GB da verificare"
        upgrade_32 = 0.0 if ram_gb >= 32 else float(ram_32gb_kit_eur)

    return {
        "listing_price_eur": listing_price,
        "upgrade_to_16_eur": upgrade_16,
        "upgrade_to_32_eur": upgrade_32,
        "total_to_16_eur": listing_price + upgrade_16 if listing_price is not None and upgrade_16 is not None else None,
        "total_to_32_eur": listing_price + upgrade_32 if listing_price is not None and upgrade_32 is not None else None,
        "ram_upgrade_note": note,
    }


def analyze_hardware(
    title: str,
    description: str = "",
    price: str = "",
    ram_8gb_upgrade_eur: float = DEFAULT_RAM_8GB_UPGRADE_EUR,
    ram_32gb_kit_eur: float = DEFAULT_RAM_32GB_KIT_EUR,
) -> dict[str, Any]:
    """Extract a small structured hardware profile and an explainable ranking."""
    title = title or ""
    description = description or ""
    text = f"{title}\n{description}"
    cpu_name, cpu = _canonical_cpu(text)
    ram_gb = _find_ram_gb(text)
    storage = _find_storage(text)
    model = _find_model(title) or _find_model(text)
    target_system, target_family = _is_target_system(text)

    form_factor = None
    form_patterns = [
        (r"\bTiny\b", "Tiny"),
        (r"\bMicro(?:\s+Form\s+Factor)?\b", "Micro"),
        (r"\bMini(?:\s*PC)?\b", "Mini"),
        (r"\bUSFF\b", "USFF"),
        (r"\bDM\b", "Desktop Mini"),
    ]
    for pattern, label in form_patterns:
        if re.search(pattern, text, re.I):
            form_factor = label
            break

    # Detect explicitly named discrete graphics separately from integrated GPU.
    discrete_match = re.search(r"\b(?:NVIDIA\s*)?(RTX|GTX)\s*\d{3,4}(?:\s*Ti|\s*SUPER)?\b|\bRadeon\s+RX\s*\d{3,4}\b", text, re.I)
    explicit_gpu = re.search(r"\b(?:Intel\s+)?(?:UHD|HD\s+Graphics|Iris\s+Xe)\s*(?:Graphics\s*)?\d{0,4}\b|\bRadeon\s+(?:Vega\s*\d+|\d{3,4}M|\d{3,4})\b", text, re.I)
    gpu = (discrete_match.group(0).strip() if discrete_match else None)
    if not gpu and explicit_gpu:
        gpu = re.sub(r"\s+", " ", explicit_gpu.group(0)).strip()
    if not gpu and cpu:
        gpu = cpu.get("gpu")

    known_cores = cpu.get("cores") if cpu else None
    known_threads = cpu.get("threads") if cpu else None
    if known_cores is not None:
        cpu_detail = f"{known_cores} core / {known_threads} thread"
        if cpu.get("tdp_w"):
            cpu_detail += f" · TDP nominale {cpu['tdp_w']} W"
    else:
        cpu_detail = "Modello non riconosciuto con sufficiente certezza"

    pricing = _upgrade_estimates(
        ram_gb, price, ram_8gb_upgrade_eur, ram_32gb_kit_eur
    )
    score_parts = _score(
        cpu_name=cpu_name,
        ram_gb=ram_gb,
        storage=storage,
        model=model,
        target_system=target_system,
        pricing=pricing,
    )
    score = score_parts["total"]
    if not target_system:
        verdict = "Fuori target: serve EliteDesk Mini, ThinkCentre Tiny o OptiPlex Micro"
    elif score >= 78:
        verdict = "Candidato interessante"
    elif score >= 58:
        verdict = "Da valutare"
    else:
        verdict = "Debole per il tuo obiettivo Proxmox"

    missing = []
    if not target_system:
        missing.append("fuori target: famiglia/chassis non confermati come Mini/Tiny/Micro richiesto")
    if not cpu_name:
        missing.append("CPU esatta/core da verificare")
    if ram_gb is None:
        missing.append("RAM installata da verificare")
    if not storage:
        missing.append("tipo e capacità storage da verificare")
    if not form_factor:
        missing.append("formato Mini/Tiny/Micro da confermare")
    missing.append("verificare RAM massima, slot liberi, alimentatore e stato reale")
    missing.append("verificare espansione storage e compatibilità di eventuali upgrade")
    if pricing.get("ram_upgrade_note"):
        missing.append(pricing["ram_upgrade_note"])

    ai_note = _ai_note(gpu, bool(discrete_match), cpu_name)
    return {
        "model": model,
        "cpu": cpu_name,
        "cpu_detail": cpu_detail,
        "cores": known_cores,
        "threads": known_threads,
        "ram_gb": ram_gb,
        "storage": storage,
        "gpu": gpu,
        "form_factor": form_factor,
        "target_system": target_system,
        "target_family": target_family,
        "score": score,
        "score_parts": score_parts,
        "verdict": verdict,
        "missing": missing,
        "ai_note": ai_note,
        "price": price,
        **pricing,
        "description": re.sub(r"\s+", " ", description).strip()[:MAX_DESCRIPTION_CHARS],
        "spec_source": "specifiche di riferimento locali + testo annuncio; non è un test del dispositivo",
    }


def _score(
    cpu_name: str | None,
    ram_gb: int | None,
    storage: list[str],
    model: str | None,
    target_system: bool,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    """Return 100 explainable points: CPU 50, price 25, RAM 10, storage 15."""
    if cpu_name:
        cpu_label = cpu_name.lower()
        for prefix in ("intel core ", "intel processor ", "amd "):
            if cpu_label.startswith(prefix):
                cpu_label = cpu_label[len(prefix):]
                break
        cpu_key = re.sub(r"[^a-z0-9]", "", cpu_label)
        # CPU names in the catalog can include spaces; map them to catalog keys.
        cpu_points = CPU_PERFORMANCE_POINTS.get(cpu_key, 8)
    else:
        cpu_points = 0

    effective_price = pricing.get("total_to_16_eur")
    if effective_price is None:
        effective_price = pricing.get("listing_price_eur")
    if effective_price is None:
        price_points = 0
    elif effective_price <= 200:
        price_points = 25
    elif effective_price <= 250:
        price_points = 23
    elif effective_price <= 300:
        price_points = 20
    elif effective_price <= 350:
        price_points = 17
    elif effective_price <= 400:
        price_points = 14
    elif effective_price <= 450:
        price_points = 11
    elif effective_price <= 500:
        price_points = 8
    elif effective_price <= 600:
        price_points = 4
    else:
        price_points = 0

    if ram_gb is None:
        ram_points = 4
    elif ram_gb >= 32:
        ram_points = 10
    elif ram_gb >= 16:
        ram_points = 8
    elif ram_gb >= 8:
        ram_points = 6
    else:
        ram_points = 2

    max_gb = 0.0
    storage_types: list[str] = []
    for value in storage:
        match = re.search(r"([\d.]+)\s*(GB|TB)", value, re.I)
        if match:
            amount = float(match.group(1)) * (1024 if match.group(2).upper() == "TB" else 1)
            max_gb = max(max_gb, amount)
        storage_types.append(value.upper())
    if max_gb >= 1000:
        capacity_points = 8
    elif max_gb >= 512:
        capacity_points = 7
    elif max_gb >= 256:
        capacity_points = 6
    elif max_gb >= 128:
        capacity_points = 4
    elif max_gb:
        capacity_points = 2
    else:
        capacity_points = 1

    if any("NVME" in item for item in storage_types):
        type_points = 3
    elif any("SSD" in item for item in storage_types):
        type_points = 2
    elif any("HDD" in item or "SSHD" in item for item in storage_types):
        type_points = 1
    else:
        type_points = 0
    multi_drive_points = 2 if len(storage) >= 2 else 0
    model_points = 2 if model else 0
    storage_points = min(15, capacity_points + type_points + multi_drive_points + model_points)

    total = cpu_points + price_points + ram_points + storage_points
    # Not-target listings are filtered by the scanner; keep the score informative
    # in dry-run output without letting chassis/GPU bonuses distort the ranking.
    return {
        "cpu": cpu_points,
        "price": price_points,
        "ram": ram_points,
        "storage_expandability": storage_points,
        "total": max(0, min(100, total)),
        "target_system": target_system,
    }

def _ai_note(gpu: str | None, discrete_gpu: bool, cpu_name: str | None) -> str:
    if discrete_gpu:
        return f"GPU dedicata citata ({gpu}); verificare VRAM e compatibilità del telaio/alimentazione."
    if gpu and re.search(r"Radeon\s+(?:680M|780M|890M|880M)", gpu, re.I):
        return "iGPU relativamente capace per esperimenti leggeri; non equivale a una GPU dedicata con VRAM."
    if gpu and re.search(r"(?:UHD|HD Graphics|N100)", gpu, re.I):
        return "Grafica integrata di base; AI locale soprattutto su CPU e con modelli piccoli."
    if cpu_name:
        return "Nessuna GPU dedicata confermata; per AI locale conta molto il modello specifico e la RAM."
    return "Dati insufficienti per stimare l'accelerazione AI; GPU e VRAM non confermate."


def enrich_item(item: dict[str, Any], session: Any = None, fetch_details: bool = True) -> dict[str, Any]:
    """Attach hardware_analysis to a normalized scanner item, non-fatally."""
    description = str(item.get("description") or "").strip()
    if fetch_details and not description:
        description = fetch_listing_description(str(item.get("url") or ""), session=session)
    try:
        import Config
        ram_8gb_upgrade_eur = float(getattr(Config, "hardware_ram_8gb_upgrade_eur", DEFAULT_RAM_8GB_UPGRADE_EUR))
        ram_32gb_kit_eur = float(getattr(Config, "hardware_ram_32gb_kit_eur", DEFAULT_RAM_32GB_KIT_EUR))
    except (ImportError, TypeError, ValueError):
        ram_8gb_upgrade_eur = DEFAULT_RAM_8GB_UPGRADE_EUR
        ram_32gb_kit_eur = DEFAULT_RAM_32GB_KIT_EUR
    analysis = analyze_hardware(
        str(item.get("title") or ""),
        description,
        str(item.get("price") or ""),
        ram_8gb_upgrade_eur=ram_8gb_upgrade_eur,
        ram_32gb_kit_eur=ram_32gb_kit_eur,
    )
    item["description"] = description
    item["hardware_analysis"] = analysis
    return item


def build_telegram_lines(item: dict[str, Any], matched_query: str | None = None) -> list[str]:
    """Build plain-text lines; the scanner's Telegram sender HTML-escapes them."""
    analysis = item.get("hardware_analysis") or analyze_hardware(
        str(item.get("title") or ""), str(item.get("description") or ""), str(item.get("price") or "")
    )
    lines = ["", "──────── HARDWARE ANALYSIS ────────"]
    if analysis.get("model"):
        lines.append(f"🖥️ Model: {analysis['model']}")
    lines.append(f"🧠 CPU: {analysis.get('cpu') or 'Not identified'}")
    lines.append(f"   {analysis.get('cpu_detail')}")
    lines.append(f"🧮 RAM: {analysis['ram_gb']} GB" if analysis.get("ram_gb") is not None else "🧮 RAM: Not identified")
    lines.append("💾 Storage: " + (" + ".join(analysis["storage"]) if analysis.get("storage") else "Not identified"))
    lines.append(f"🎮 GPU: {analysis.get('gpu') or 'Not identified'}")
    lines.append(f"📦 Form factor: {analysis.get('form_factor') or 'Not confirmed'}")
    lines.append(f"🎯 Target chassis: {'YES — ' + analysis['target_family'] if analysis.get('target_system') else 'NO / NOT CONFIRMED'}")
    parts = analysis.get("score_parts", {})
    lines.append(
        f"📊 Score: {analysis.get('score', 0)}/100 "
        f"(CPU {parts.get('cpu', 0)}/50 · price {parts.get('price', 0)}/25 · "
        f"RAM {parts.get('ram', 0)}/10 · storage/expansion {parts.get('storage_expandability', 0)}/15)"
    )
    lines.append(f"⚙️ Proxmox suitability: {analysis.get('verdict')}")
    if analysis.get("total_to_16_eur") is not None:
        lines.append(f"💶 Total estimate with 16 GB: €{analysis['total_to_16_eur']:.2f} (listing + estimated RAM; shipping/buyer fee excluded)")
    else:
        lines.append("💶 Total estimate with 16 GB: to verify RAM configuration")
    if analysis.get("total_to_32_eur") is not None:
        lines.append(f"💶 Total estimate with 32 GB: €{analysis['total_to_32_eur']:.2f} (listing + estimated RAM kit; shipping/buyer fee excluded)")
    else:
        lines.append("💶 Total estimate with 32 GB: to verify RAM configuration")
    lines.append(f"🤖 AI: {analysis.get('ai_note')}")
    if analysis.get("description"):
        desc = analysis["description"]
        if len(desc) > MAX_DESCRIPTION_CHARS:
            desc = desc[:MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"
        lines.append(f"📝 Description: {desc}")
    if analysis.get("missing"):
        lines.append("🔎 Check: " + "; ".join(analysis["missing"]))
    lines.append("ℹ️ Specs are inferred from listing text and the local CPU catalog; verify with the seller.")
    return lines

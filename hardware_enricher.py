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

import message_format as fmt


REQUEST_TIMEOUT_SECONDS = 15
MAX_DESCRIPTION_CHARS = 520
# Listing text kept for analysis; the CPU is often stated after the first lines.
MAX_ANALYSIS_TEXT_CHARS = 5000
DEFAULT_RAM_8GB_UPGRADE_EUR = 50
DEFAULT_RAM_32GB_KIT_EUR = 130
DEFAULT_RAM_16GB_MODULE_EUR = 65
RAM_MODULE_SIZES_GB = {2, 4, 8, 16, 32, 64}
PLAUSIBLE_RAM_SIZES_GB = {2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64}

# Score weights: CPU performance (a 0-1 fraction) is scaled to 40 points.
CPU_WEIGHT = 0.8
# Benchmark-based CPU performance: PassMark multithread and single-thread ratings
# blended 70/30, each relative to a ceiling CPU that earns full points. Faster
# parts are capped at full points rather than extending the scale.
BENCHMARK_CEILING_CPU = "i712700t"
BENCHMARK_MT_SHARE = 0.7
# Comparison anchors shown in alerts (catalog keys or CPU names): the current
# homelab node and the CPU the next node is aimed at. Not minimum requirements.
DEFAULT_REFERENCE_CPUS = ("n100", "i512500t")
# Unknown CPU on a known platform: typical CPU points discounted for uncertainty.
ESTIMATED_CPU_FACTOR = 0.7
# Unknown CPU on an unknown platform: neutral, not worse than a Celeron.
UNKNOWN_CPU_POINTS = 8
# Default maximum points per score component; they add up to 100. Override
# with hardware_score_weights in Config.py: values are relative weights and are
# rescaled to a 100-point total.
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {"cpu": 40, "price": 25, "ram": 10, "storage": 10, "platform": 15}
STRONG_CANDIDATE_SCORE = 70
WORTH_EVALUATING_SCORE = 55

# Fallback CPU capability tiers (0-50), used only for a CPU without benchmark
# data in CPU_BENCHMARKS. These are curated heuristics, not benchmark results.
CPU_PERFORMANCE_POINTS: dict[str, int] = {
    "g3900t": 2, "g3930t": 2, "g4400t": 3, "g4500t": 3,
    "g4560t": 4, "g4600t": 4, "g4900t": 3, "g4930t": 3,
    "g5400t": 5, "g5420t": 5, "g5500t": 5, "g5600t": 5,
    "i36100t": 9, "i37100t": 11, "i38100t": 17, "i39100t": 18, "i310100t": 22,
    "i56400t": 17, "i56500": 21, "i56500t": 19, "i56600t": 21,
    "i57400t": 20, "i57500": 23, "i57500t": 22, "i57600": 26, "i57600t": 23,
    "i58400": 33, "i58600": 36, "i59400": 35, "i59400t": 31, "i59600t": 36,
    "i510400t": 36, "i510600t": 40, "i76700": 27, "i77700": 30,
    "ryzen3pro3200ge": 19, "ryzen5pro3400ge": 25,
    "ryzen3pro3200g": 21, "ryzen5pro3400g": 27, "ryzen3pro2200g": 18, "ryzen5pro2400g": 24,
    "a10pro9700e": 8, "a12pro9800e": 9,
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
    "g3900t": {"name": "Intel Celeron G3900T", "cores": 2, "threads": 2, "tdp_w": 35, "gpu": "Intel HD Graphics 510"},
    "g3930t": {"name": "Intel Celeron G3930T", "cores": 2, "threads": 2, "tdp_w": 35, "gpu": "Intel HD Graphics 610"},
    "g4400t": {"name": "Intel Pentium G4400T", "cores": 2, "threads": 2, "tdp_w": 35, "gpu": "Intel HD Graphics 510"},
    "g4500t": {"name": "Intel Pentium G4500T", "cores": 2, "threads": 2, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
    "g4560t": {"name": "Intel Pentium G4560T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 610"},
    "g4600t": {"name": "Intel Pentium G4600T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 630"},
    "g4900t": {"name": "Intel Celeron G4900T", "cores": 2, "threads": 2, "tdp_w": 35, "gpu": "Intel UHD Graphics 610"},
    "g4930t": {"name": "Intel Celeron G4930T", "cores": 2, "threads": 2, "tdp_w": 35, "gpu": "Intel UHD Graphics 610"},
    "g5400t": {"name": "Intel Pentium Gold G5400T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 610"},
    "g5420t": {"name": "Intel Pentium Gold G5420T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 610"},
    "g5500t": {"name": "Intel Pentium Gold G5500T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "g5600t": {"name": "Intel Pentium Gold G5600T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i56400t": {"name": "Intel Core i5-6400T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
    "i56500t": {"name": "Intel Core i5-6500T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
    "i56600t": {"name": "Intel Core i5-6600T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
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
    "i36100t": {"name": "Intel Core i3-6100T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 530"},
    "i37100t": {"name": "Intel Core i3-7100T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 630"},
    "i38100t": {"name": "Intel Core i3-8100T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i39100t": {"name": "Intel Core i3-9100T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i310100t": {"name": "Intel Core i3-10100T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i56500": {"name": "Intel Core i5-6500", "cores": 4, "threads": 4, "tdp_w": 65, "gpu": "Intel HD Graphics 530"},
    "i57400t": {"name": "Intel Core i5-7400T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 630"},
    "i57600": {"name": "Intel Core i5-7600", "cores": 4, "threads": 4, "tdp_w": 65, "gpu": "Intel HD Graphics 630"},
    "i57600t": {"name": "Intel Core i5-7600T", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Intel HD Graphics 630"},
    "i58400": {"name": "Intel Core i5-8400", "cores": 6, "threads": 6, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i58600": {"name": "Intel Core i5-8600", "cores": 6, "threads": 6, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i59400": {"name": "Intel Core i5-9400", "cores": 6, "threads": 6, "tdp_w": 65, "gpu": "Intel UHD Graphics 630"},
    "i59400t": {"name": "Intel Core i5-9400T", "cores": 6, "threads": 6, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i59600t": {"name": "Intel Core i5-9600T", "cores": 6, "threads": 6, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i510400t": {"name": "Intel Core i5-10400T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i510600t": {"name": "Intel Core i5-10600T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i76700": {"name": "Intel Core i7-6700", "cores": 4, "threads": 8, "tdp_w": 65, "gpu": "Intel HD Graphics 530"},
    "i77700": {"name": "Intel Core i7-7700", "cores": 4, "threads": 8, "tdp_w": 65, "gpu": "Intel HD Graphics 630"},
    "ryzen3pro3200g": {"name": "AMD Ryzen 3 PRO 3200G", "cores": 4, "threads": 4, "tdp_w": 65, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro3400g": {"name": "AMD Ryzen 5 PRO 3400G", "cores": 4, "threads": 8, "tdp_w": 65, "gpu": "Radeon Vega 11 Graphics"},
    "ryzen3pro2200g": {"name": "AMD Ryzen 3 PRO 2200G", "cores": 4, "threads": 4, "tdp_w": 65, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro2400g": {"name": "AMD Ryzen 5 PRO 2400G", "cores": 4, "threads": 8, "tdp_w": 65, "gpu": "Radeon Vega 11 Graphics"},
    "ryzen3pro3200ge": {"name": "AMD Ryzen 3 PRO 3200GE", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro3400ge": {"name": "AMD Ryzen 5 PRO 3400GE", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Radeon Vega 11 Graphics"},
    "a10pro9700e": {"name": "AMD PRO A10-9700E", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Radeon R7 Graphics"},
    "a12pro9800e": {"name": "AMD PRO A12-9800E", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Radeon R7 Graphics"},
    "n100": {"name": "Intel Processor N100", "cores": 4, "threads": 4, "tdp_w": 6, "gpu": "Intel UHD Graphics (Alder Lake-N)"},
    "ryzen3pro2200ge": {"name": "AMD Ryzen 3 PRO 2200GE", "cores": 4, "threads": 4, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro2400ge": {"name": "AMD Ryzen 5 PRO 2400GE", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Radeon Vega 11 Graphics"},
    "ryzen5pro4650ge": {"name": "AMD Ryzen 5 PRO 4650GE", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Radeon Vega 7 Graphics"},
    "ryzen7pro4750ge": {"name": "AMD Ryzen 7 PRO 4750GE", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen5pro5650ge": {"name": "AMD Ryzen 5 PRO 5650GE", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Radeon Vega 7 Graphics"},
    "ryzen7pro5750ge": {"name": "AMD Ryzen 7 PRO 5750GE", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Radeon Vega 8 Graphics"},
    "ryzen56600h": {"name": "AMD Ryzen 5 6600H", "cores": 6, "threads": 12, "tdp_w": 45, "gpu": "AMD Radeon 660M"},
    "ryzen76800h": {"name": "AMD Ryzen 7 6800H", "cores": 8, "threads": 16, "tdp_w": 45, "gpu": "AMD Radeon 680M"},
    "i511400t": {"name": "Intel Core i5-11400T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 730"},
    "i511600t": {"name": "Intel Core i5-11600T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 750"},
    "i312100t": {"name": "Intel Core i3-12100T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel UHD Graphics 730"},
    "i512400t": {"name": "Intel Core i5-12400T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 730"},
    "i512600t": {"name": "Intel Core i5-12600T", "cores": 6, "threads": 12, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "i513400t": {"name": "Intel Core i5-13400T", "cores": 10, "threads": 16, "tdp_w": 35, "gpu": "Intel UHD Graphics 730"},
    "i513500t": {"name": "Intel Core i5-13500T", "cores": 14, "threads": 20, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "i713700t": {"name": "Intel Core i7-13700T", "cores": 16, "threads": 24, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "i514500t": {"name": "Intel Core i5-14500T", "cores": 14, "threads": 20, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "i310300t": {"name": "Intel Core i3-10300T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i310105t": {"name": "Intel Core i3-10105T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i99900t": {"name": "Intel Core i9-9900T", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "i910900t": {"name": "Intel Core i9-10900T", "cores": 10, "threads": 20, "tdp_w": 35, "gpu": "Intel UHD Graphics 630"},
    "g6400t": {"name": "Intel Pentium Gold G6400T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 610"},
    "g7400t": {"name": "Intel Pentium Gold G7400T", "cores": 2, "threads": 4, "tdp_w": 35, "gpu": "Intel UHD Graphics 710"},
    "i313100t": {"name": "Intel Core i3-13100T", "cores": 4, "threads": 8, "tdp_w": 35, "gpu": "Intel UHD Graphics 730"},
    "i513600t": {"name": "Intel Core i5-13600T", "cores": 14, "threads": 20, "tdp_w": 35, "gpu": "Intel UHD Graphics 770"},
    "i911900t": {"name": "Intel Core i9-11900T", "cores": 8, "threads": 16, "tdp_w": 35, "gpu": "Intel UHD Graphics 750"},
}

# PassMark CPU Mark ratings as (single thread, multithread), copied once from
# cpubenchmark.net's CPU lists and per-CPU pages. They are averages of user
# submissions, not a test of the listed machine. Refresh by hand (never during a
# scan); a CPU without an entry falls back to CPU_PERFORMANCE_POINTS.
BENCHMARK_SOURCE = "PassMark CPU Mark"
BENCHMARK_VERSION = "cpubenchmark.net, retrieved 2026-11-09"
CPU_BENCHMARKS: dict[str, tuple[int, int]] = {
    "g3900t": (1529, 1948), "g3930t": (1655, 2023), "g4400t": (1743, 2318), "g4500t": (1856, 2411),
    "g4560t": (1726, 2976), "g4600t": (1775, 3062), "g4900t": (1733, 2236), "g4930t": (1773, 2287),
    "g5400t": (1793, 3161), "g5420t": (1899, 3330), "g5500t": (1890, 3293), "g5600t": (2000, 3496),
    "i56400t": (1606, 4258), "i56500t": (1773, 4763), "i56600t": (2055, 5588), "i57500": (2244, 6045),
    "i57500t": (1918, 5241), "i58400t": (1899, 7394), "i58500": (2428, 9501), "i58500t": (1996, 7724),
    "i58600t": (2261, 9149), "i76700t": (2007, 6975), "i78700": (2624, 12731), "i77700t": (2131, 7547),
    "i78700t": (2299, 10212), "i59500": (2564, 9841), "i59500t": (2105, 8008), "i79700": (2742, 13167),
    "i79700t": (2418, 10581), "i510400": (2555, 11956), "i510500": (2777, 12872), "i510500t": (2283, 10238),
    "i710700": (2879, 15963), "i710700t": (2568, 12801), "i511500": (3131, 17055), "i511500t": (2536, 12782),
    "i711700": (3253, 20564), "i711700t": (2850, 15199), "i512500": (3645, 19680), "i512500t": (3440, 16310),
    "i712700": (3839, 29737), "i712700t": (3558, 21129), "i36100t": (1854, 3635), "i37100t": (1911, 3728),
    "i38100t": (1855, 5249), "i39100t": (2116, 5637), "i310100t": (2255, 7347), "i56500": (2087, 5597),
    "i57400t": (1733, 4673), "i57600": (2449, 6583), "i57600t": (2138, 5844), "i58400": (2364, 9236),
    "i58600": (2554, 9990), "i59400": (2412, 9347), "i59400t": (2058, 8092), "i59600t": (2416, 9568),
    "i510400t": (2124, 9769), "i510600t": (2460, 11482), "i76700": (2274, 8032), "i77700": (2441, 8638),
    "ryzen3pro3200g": (2112, 6563), "ryzen5pro3400g": (2281, 9004), "ryzen3pro2200g": (2035, 6607), "ryzen5pro2400g": (2138, 8546),
    "ryzen3pro3200ge": (2117, 6732), "ryzen5pro3400ge": (2085, 7770), "a10pro9700e": (1454, 3059), "a12pro9800e": (1469, 3049),
    "n100": (1879, 5304), "ryzen3pro2200ge": (1991, 6099), "ryzen5pro2400ge": (2121, 7801), "ryzen5pro4650ge": (2609, 15301),
    "ryzen7pro4750ge": (2677, 18436), "ryzen5pro5650ge": (3187, 17964), "ryzen7pro5750ge": (3252, 20998), "ryzen56600h": (3133, 17959),
    "ryzen76800h": (3177, 22651), "i511400t": (2475, 13104), "i312100t": (3221, 12392), "i512400t": (3331, 15575),
    "i512600t": (3549, 17444), "i513500t": (3578, 22411), "i513400t": (3474, 20095), "i713700t": (3801, 27130),
    "i514500t": (3735, 22840), "i310300t": (2291, 7876), "i511600t": (2762, 14649), "i310105t": (2355, 7803),
    "g6400t": (2102, 3565), "i99900t": (2456, 13029), "i910900t": (2519, 14714),
    "g7400t": (2348, 5274), "i313100t": (3359, 12738), "i513600t": (3775, 27789), "i911900t": (3208, 18245),
}

# Microarchitecture and launch year. Intel Core parts follow their generation;
# the rest are listed explicitly. Approximate launch years, for context only.
CORE_GENERATION_ARCHITECTURES: dict[int, tuple[str, int]] = {
    6: ("Skylake", 2015), 7: ("Kaby Lake", 2017), 8: ("Coffee Lake", 2018),
    9: ("Coffee Lake Refresh", 2019), 10: ("Comet Lake", 2020), 11: ("Rocket Lake", 2021),
    12: ("Alder Lake", 2022), 13: ("Raptor Lake", 2023), 14: ("Raptor Lake Refresh", 2024),
}
CPU_ARCHITECTURES: dict[str, tuple[str, int]] = {
    "g3900t": ("Skylake", 2015), "g4400t": ("Skylake", 2015), "g4500t": ("Skylake", 2015),
    "g3930t": ("Kaby Lake", 2017), "g4560t": ("Kaby Lake", 2017), "g4600t": ("Kaby Lake", 2017),
    "g4900t": ("Coffee Lake", 2018), "g5400t": ("Coffee Lake", 2018), "g5500t": ("Coffee Lake", 2018),
    "g5600t": ("Coffee Lake", 2018), "g4930t": ("Coffee Lake", 2019), "g5420t": ("Coffee Lake", 2019),
    "g6400t": ("Comet Lake", 2020), "g7400t": ("Alder Lake", 2022),
    # Desktop 8th-gen non-T parts launched in late 2017, the T parts in 2018.
    "i58400": ("Coffee Lake", 2017), "i58600": ("Coffee Lake", 2017), "i78700": ("Coffee Lake", 2017),
    "n100": ("Alder Lake-N", 2023),
    "a10pro9700e": ("Bristol Ridge (Excavator)", 2016), "a12pro9800e": ("Bristol Ridge (Excavator)", 2016),
    "ryzen3pro2200g": ("Raven Ridge (Zen)", 2018), "ryzen5pro2400g": ("Raven Ridge (Zen)", 2018),
    "ryzen3pro2200ge": ("Raven Ridge (Zen)", 2018), "ryzen5pro2400ge": ("Raven Ridge (Zen)", 2018),
    "ryzen3pro3200g": ("Picasso (Zen+)", 2019), "ryzen5pro3400g": ("Picasso (Zen+)", 2019),
    "ryzen3pro3200ge": ("Picasso (Zen+)", 2019), "ryzen5pro3400ge": ("Picasso (Zen+)", 2019),
    "ryzen5pro4650ge": ("Renoir (Zen 2)", 2020), "ryzen7pro4750ge": ("Renoir (Zen 2)", 2020),
    "ryzen5pro5650ge": ("Cezanne (Zen 3)", 2021), "ryzen7pro5750ge": ("Cezanne (Zen 3)", 2021),
    "ryzen56600h": ("Rembrandt (Zen 3+)", 2022), "ryzen76800h": ("Rembrandt (Zen 3+)", 2022),
}


def _merge_reference_data() -> None:
    """Attach benchmark and architecture fields to CPU_CATALOG entries."""
    for key, specification in CPU_CATALOG.items():
        if key in CPU_BENCHMARKS:
            specification["bench_st"], specification["bench_mt"] = CPU_BENCHMARKS[key]
        match = re.fullmatch(r"i[3579](\d{4,5})[a-z]*", key)
        generation = int(match.group(1)[:-3]) if match else None
        architecture = CPU_ARCHITECTURES.get(key) or CORE_GENERATION_ARCHITECTURES.get(generation)
        if architecture:
            specification["architecture"], specification["launch_year"] = architecture


_merge_reference_data()

# Parse longer / exact models first, so e.g. i5-7500T cannot be reduced to i5-7500.
INTEL_CPU_RE = re.compile(
    r"\b(?:intel\s*)?(?:core\s*)?i\s*-?\s*([3579])\s*[- ]?\s*(\d{4,5})([a-z]{0,2})\b", re.I
)
# Sellers often write only the number of a low-power part: "8500T/256go/8go".
BARE_T_CPU_RE = re.compile(r"(?<![\w-])(\d{4,5})t\b", re.I)
AMD_A_SERIES_RE = re.compile(r"\b(?:amd\s*)?(?:pro\s*)?a(10|12)\s*-?\s*(9[78]00e)\b", re.I)
AMD_CPU_RE = re.compile(r"\bryzen\s*([3579])\s*(pro\s*)?(\d{4,5}[a-z]{0,2})\b", re.I)
N100_RE = re.compile(r"\b(?:intel\s+processor\s+)?n100\b", re.I)
ENTRY_LEVEL_CPU_RE = re.compile(
    r"\b(?:intel\s+)?(?:celeron|pentium(?:\s+gold)?|gold)\s*(g\d{4,5}[a-z]{0,2})\b",
    re.I,
)

# These are upgrade candidates found in the manufacturers' platform references.
# They are not a promise that every regional MTM/BIOS/PSU configuration accepts
# the part: the exact machine type and thermal solution must still be checked.
PLATFORM_UPGRADE_PROFILES: list[dict[str, Any]] = [
    {
        "pattern": re.compile(r"\bthinkcentre\s+m700\b", re.I),
        "candidate_key": "i76700t",
        "platform": "Lenovo ThinkCentre M700 Tiny",
        "reference": "Lenovo PSREF — M700 Tiny lists the i7-6700T as its top CPU option; 4 cores / 8 threads.",
        "source_url": "https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M700_Tiny/ThinkCentre_M700_Tiny_Spec.PDF",
    },
    {
        "pattern": re.compile(r"\bthinkcentre\s+m710(?:q)?\b", re.I),
        "candidate_key": "i77700t",
        "platform": "Lenovo ThinkCentre M710 Tiny",
        "reference": "Lenovo PSREF — M710 Tiny lists the i7-7700T; 4 cores / 8 threads.",
        "source_url": "https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M710_Tiny/ThinkCentre_M710_Tiny_Spec.pdf",
    },
    {
        "pattern": re.compile(r"\bthinkcentre\s+m720q?\b", re.I),
        "candidate_key": "i78700t",
        "platform": "Lenovo ThinkCentre M720q Tiny",
        "reference": "Lenovo PSREF lists the i7-8700T as a 6-core / 12-thread option; later 8/9th-gen SKUs are also listed.",
        "source_url": "https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M720_Tiny/ThinkCentre_M720_Tiny_Spec.PDF",
    },
    {
        "pattern": re.compile(r"\bthinkcentre\s+m920q?\b", re.I),
        "candidate_key": "i78700t",
        "platform": "Lenovo ThinkCentre M920q Tiny",
        "reference": "Lenovo PSREF includes an M920q configuration with i7-8700T; 6 cores / 12 threads.",
        "source_url": "https://psref.lenovo.com/Detail/ThinkCentre/ThinkCentre_M920_Tiny?M=10RS001PUS",
    },
    {
        "pattern": re.compile(r"\b(?:hp\s+)?elitedesk\s+800\s+g2\b", re.I),
        "candidate_key": "i76700t",
        "platform": "HP EliteDesk 800 G2 Desktop Mini",
        "reference": "HP QuickSpecs list the i7-6700T for the Desktop Mini 35W model only.",
        "source_url": "https://h30434.www3.hp.com/psg/attachments/psg/Business-PC-Workstation-POS/56278/1/HP%20EliteDesk%20800%20G2%20QuickSpecs%20v31.pdf",
    },
]


# Intel vPro (AMT) availability per CPU SKU. Parts in neither set are reported
# as "to verify". AMD parts never provide Intel AMT.
VPRO_CPU_KEYS = {
    "i56500", "i56500t", "i56600t", "i57500", "i57500t", "i57600", "i57600t",
    "i58500", "i58500t", "i58600", "i58600t", "i59500", "i59500t",
    "i510500", "i510500t", "i511500", "i511500t", "i512500", "i512500t",
    "i76700", "i76700t", "i77700", "i77700t", "i78700", "i78700t",
    "i79700", "i79700t", "i710700", "i710700t", "i711700", "i711700t",
    "i712700", "i712700t", "i512600t", "i513500t", "i514500t", "i713700t",
    "i513600t", "i911900t",
}
NON_VPRO_CPU_KEYS = {
    "g3900t", "g3930t", "g4400t", "g4500t", "g4560t", "g4600t", "g4900t", "g4930t",
    "g5400t", "g5420t", "g5500t", "g5600t", "n100",
    "i36100t", "i37100t", "i38100t", "i39100t", "i310100t",
    "i56400t", "i57400t", "i58400", "i58400t", "i59400", "i59400t",
    "i510400", "i510400t", "i511400t", "i512400t", "i513400t",
    "i312100t", "i310300t", "i310105t", "g6400t", "g7400t", "i313100t",
}


def _platform(
    pattern: str, name: str, generation: int | None, vpro: str, max_ram_gb: int,
    pcie_slot: bool | None, max_tdp_w: int | None, typical_cpu: str,
    cpu_generations: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    if cpu_generations is None:
        cpu_generations = (generation,) if generation else ()
    return {
        "pattern": re.compile(pattern, re.I), "name": name, "generation": generation,
        "vpro": vpro, "max_ram_gb": max_ram_gb, "pcie_slot": pcie_slot,
        "max_tdp_w": max_tdp_w, "typical_cpu": typical_cpu,
        "cpu_generations": cpu_generations,
    }


# Platform facts for the supported chassis, from Lenovo PSREF, HP QuickSpecs and
# Dell OptiPlex Micro manuals. "vpro" describes the chipset: "yes" (Q-series,
# AMT needs a vPro CPU too), "no", "verify" (not confirmed here) or "amd".
# max_ram_gb is the official maximum; max_tdp_w is the highest CPU class the
# chassis ships with (35 = T-series only). None means "not confirmed here".
# cpu_generations lists the Intel Core generations the model was offered with.
_GEN = r"\s*(?:Gen\.?|G)\s*"
PLATFORM_PROFILES: list[dict[str, Any]] = [
    # 11th-13th gen. Lenovo values from the PSREF spec sheets of each model
    # (psref.lenovo.com, ThinkCentre_<model>_Spec.pdf, read 2026-11-09). HP and
    # Dell values from the HP 800 G8 Desktop Mini datasheet and the Dell OptiPlex
    # 7090 Micro Setup and Specifications; HP QuickSpecs could not be read, so
    # unconfirmed HP fields stay None ("to verify").
    _platform(rf"\bM70q{_GEN}4\b", "Lenovo ThinkCentre M70q Gen 4 Tiny", 13, "yes", 64, False, 35, "i513400t"),
    _platform(rf"\bM70q{_GEN}3\b", "Lenovo ThinkCentre M70q Gen 3 Tiny", 12, "yes", 64, False, 35, "i512400t"),
    _platform(rf"\bM70q{_GEN}2\b", "Lenovo ThinkCentre M70q Gen 2 Tiny", 11, "verify", 64, False, 35, "i511400t", (10, 11)),
    _platform(rf"\bM80q{_GEN}3\b", "Lenovo ThinkCentre M80q Gen 3 Tiny", 12, "yes", 64, False, 35, "i512500t"),
    _platform(rf"\bM90q{_GEN}3\b", "Lenovo ThinkCentre M90q Gen 3 Tiny", 12, "yes", 64, True, 65, "i512500t"),
    _platform(rf"\bM90q{_GEN}2\b", "Lenovo ThinkCentre M90q Gen 2 Tiny", 11, "yes", 64, None, 65, "i511500t", (10, 11)),
    _platform(r"\bEliteDesk\s+800\s+G8\b", "HP EliteDesk 800 G8 Mini", 11, "yes", 64, None, None, "i511500t"),
    _platform(r"\b(?:EliteDesk|Elite\s+Mini)\s+800\s+G9\b", "HP Elite Mini 800 G9", 12, "verify", 64, None, None, "i512500t"),
    _platform(r"\bOptiPlex\s+7090\b", "Dell OptiPlex 7090 Micro", 11, "yes", 64, None, 65, "i511500t", (10, 11)),
    _platform(r"\bM920x\b", "Lenovo ThinkCentre M920x Tiny", 8, "yes", 32, True, 65, "i58500t", (8, 9)),
    _platform(r"\bM920q?\b", "Lenovo ThinkCentre M920q Tiny", 8, "yes", 32, True, 65, "i58500t", (8, 9)),
    _platform(r"\bM720q?\b", "Lenovo ThinkCentre M720q Tiny", 8, "no", 32, True, 65, "i58500t", (8, 9)),
    _platform(r"\bM910[qx]?\b", "Lenovo ThinkCentre M910q Tiny", 7, "yes", 32, False, 35, "i57500t", (6, 7)),
    _platform(r"\bM710q?\b", "Lenovo ThinkCentre M710q Tiny", 7, "no", 32, False, 35, "i57500t", (6, 7)),
    _platform(r"\bM900\b", "Lenovo ThinkCentre M900 Tiny", 6, "yes", 32, False, 35, "i56500t"),
    _platform(r"\bM700\b", "Lenovo ThinkCentre M700 Tiny", 6, "no", 32, False, 35, "i56500t"),
    _platform(r"\bM90q\b", "Lenovo ThinkCentre M90q Tiny", 10, "yes", 64, None, None, "i510500t"),
    _platform(r"\bM80q\b", "Lenovo ThinkCentre M80q Tiny", 10, "verify", 64, None, None, "i510500t"),
    _platform(r"\bM70q\b", "Lenovo ThinkCentre M70q Tiny", 10, "no", 64, None, None, "i510400t"),
    _platform(r"\bM75q\b", "Lenovo ThinkCentre M75q Tiny", None, "amd", 32, False, 35, "ryzen5pro3400ge"),
    _platform(r"\bM715q\b", "Lenovo ThinkCentre M715q Tiny", None, "amd", 32, False, 35, "ryzen5pro2400ge"),
    _platform(r"\bEliteDesk\s+800\s+G2\b", "HP EliteDesk 800 G2 Mini", 6, "yes", 32, False, 65, "i56500t"),
    _platform(r"\bEliteDesk\s+800\s+G3\b", "HP EliteDesk 800 G3 Mini", 7, "yes", 32, False, 65, "i57500t", (6, 7)),
    _platform(r"\bEliteDesk\s+800\s+G4\b", "HP EliteDesk 800 G4 Mini", 8, "yes", 32, False, 65, "i58500t"),
    _platform(r"\bEliteDesk\s+800\s+G5\b", "HP EliteDesk 800 G5 Mini", 9, "yes", 64, False, 65, "i59500t", (8, 9)),
    _platform(r"\bEliteDesk\s+800\s+G6\b", "HP EliteDesk 800 G6 Mini", 10, "yes", 64, False, 65, "i510500t"),
    _platform(r"\bEliteDesk\s+705\s+G[45]\b", "HP EliteDesk 705 Mini", None, "amd", 32, False, 35, "ryzen5pro2400ge"),
    _platform(r"\bProDesk\s+400\s+G3\b", "HP ProDesk 400 G3 Mini", 7, "no", 32, False, 35, "i57500t", (6, 7)),
    _platform(r"\bProDesk\s+400\s+G4\b", "HP ProDesk 400 G4 Mini", 8, "no", 32, False, 35, "i58500t"),
    _platform(r"\bProDesk\s+400\s+G5\b", "HP ProDesk 400 G5 Mini", 9, "no", 32, False, 35, "i59500t", (8, 9)),
    _platform(r"\bProDesk\s+400\s+G6\b", "HP ProDesk 400 G6 Mini", 10, "no", 64, False, 35, "i510500t"),
    _platform(r"\bProDesk\s+600\s+G2\b", "HP ProDesk 600 G2 Mini", 6, "verify", 32, False, 65, "i56500t"),
    _platform(r"\bProDesk\s+600\s+G3\b", "HP ProDesk 600 G3 Mini", 7, "verify", 32, False, 65, "i57500t", (6, 7)),
    _platform(r"\bProDesk\s+600\s+G4\b", "HP ProDesk 600 G4 Mini", 8, "verify", 32, False, 65, "i58500t"),
    _platform(r"\bProDesk\s+600\s+G5\b", "HP ProDesk 600 G5 Mini", 9, "verify", 64, False, 65, "i59500t", (8, 9)),
    _platform(r"\bProDesk\s+600\s+G6\b", "HP ProDesk 600 G6 Mini", 10, "verify", 64, False, 65, "i510500t"),
    _platform(r"\bProDesk\s+405\s+G\d\b", "HP ProDesk 405 Mini", None, "amd", 32, False, 35, "ryzen3pro3200ge"),
    _platform(r"\bOptiPlex\s+3040\b", "Dell OptiPlex 3040 Micro", 6, "no", 16, False, 35, "i56500t"),
    _platform(r"\bOptiPlex\s+7040\b", "Dell OptiPlex 7040 Micro", 6, "yes", 32, False, 35, "i56500t"),
    _platform(r"\bOptiPlex\s+3050\b", "Dell OptiPlex 3050 Micro", 7, "no", 32, False, 35, "i57500t", (6, 7)),
    _platform(r"\bOptiPlex\s+5050\b", "Dell OptiPlex 5050 Micro", 7, "no", 32, False, 35, "i57500t", (6, 7)),
    _platform(r"\bOptiPlex\s+7050\b", "Dell OptiPlex 7050 Micro", 7, "yes", 32, False, 35, "i57500t", (6, 7)),
    _platform(r"\bOptiPlex\s+3060\b", "Dell OptiPlex 3060 Micro", 8, "no", 32, False, 35, "i58500t"),
    _platform(r"\bOptiPlex\s+5060\b", "Dell OptiPlex 5060 Micro", 8, "verify", 32, False, 35, "i58500t"),
    _platform(r"\bOptiPlex\s+7060\b", "Dell OptiPlex 7060 Micro", 8, "yes", 32, False, 35, "i58500t"),
    _platform(r"\bOptiPlex\s+3070\b", "Dell OptiPlex 3070 Micro", 9, "no", 32, False, 35, "i59500t", (8, 9)),
    _platform(r"\bOptiPlex\s+5070\b", "Dell OptiPlex 5070 Micro", 9, "verify", 64, False, 35, "i59500t", (8, 9)),
    _platform(r"\bOptiPlex\s+7070\b", "Dell OptiPlex 7070 Micro", 9, "yes", 64, False, 35, "i59500t", (8, 9)),
    _platform(r"\bOptiPlex\s+3080\b", "Dell OptiPlex 3080 Micro", 10, "no", 64, False, 35, "i510500t"),
    _platform(r"\bOptiPlex\s+5080\b", "Dell OptiPlex 5080 Micro", 10, "verify", 64, False, 35, "i510500t"),
    _platform(r"\bOptiPlex\s+7080\b", "Dell OptiPlex 7080 Micro", 10, "yes", 64, False, 35, "i510500t"),
]


# Typical CPU per generation and tier, used only when the exact model is unknown.
TYPICAL_CPU_BY_GENERATION: dict[int, dict[str, str]] = {
    6: {"entry": "g4400t", "i3": "i36100t", "i5": "i56500t", "i7": "i76700t"},
    7: {"entry": "g4560t", "i3": "i37100t", "i5": "i57500t", "i7": "i77700t"},
    8: {"entry": "g5400t", "i3": "i38100t", "i5": "i58500t", "i7": "i78700t"},
    9: {"entry": "g5400t", "i3": "i39100t", "i5": "i59500t", "i7": "i79700t"},
    10: {"entry": "g5600t", "i3": "i310100t", "i5": "i510500t", "i7": "i710700t"},
    11: {"entry": "g6400t", "i3": "i310105t", "i5": "i511500t", "i7": "i711700t"},
    12: {"entry": "g7400t", "i3": "i312100t", "i5": "i512500t", "i7": "i712700t"},
    13: {"entry": "g7400t", "i3": "i313100t", "i5": "i513500t", "i7": "i713700t"},
}
CPU_TIER_HINT_RE = re.compile(
    r"\b(?:core\s*)?i\s*-?\s*([3579])\b|\b(celeron|pentium)\b|\b(?:amd\s+)?pro\s+(a1[02])\b",
    re.I,
)


def _estimated_cpu_key(platform: dict[str, Any] | None, text: str) -> str | None:
    """Pick a typical CPU for the platform, honoring an "i3"/"i7"/"Celeron" hint."""
    if not platform:
        return None
    match = CPU_TIER_HINT_RE.search(text or "")
    generation = platform.get("generation")
    if match and generation in TYPICAL_CPU_BY_GENERATION:
        if match.group(1):
            tier = {"3": "i3", "5": "i5"}.get(match.group(1), "i7")
        elif match.group(2):
            tier = "entry"
        else:
            tier = "i5"
        return TYPICAL_CPU_BY_GENERATION[generation][tier]
    if match and match.group(3):
        return "a12pro9800e"
    return platform["typical_cpu"]


def cpu_generation(cpu_key: str | None) -> int | None:
    """Return the Intel Core generation of a catalog key such as "i78700t"."""
    match = re.fullmatch(r"i[3579](\d{4,5})[a-z]*", cpu_key or "")
    return int(match.group(1)[:-3]) if match else None


def compatible_platforms(cpu_key: str) -> list[str]:
    """List supported chassis offered with this CPU generation and TDP class."""
    generation = cpu_generation(cpu_key)
    cpu = CPU_CATALOG.get(cpu_key)
    if generation is None or not cpu:
        return []
    names = []
    for profile in PLATFORM_PROFILES:
        if generation not in profile["cpu_generations"]:
            continue
        if profile["max_tdp_w"] is not None and cpu["tdp_w"] > profile["max_tdp_w"]:
            continue
        names.append(profile["name"])
    return names


def cpu_performance(
    cpu_key: str | None, ceiling_key: str | None = None
) -> tuple[float | None, str | None]:
    """Return (0-1 performance fraction, source) for a catalog CPU.

    Source is "benchmark" when PassMark data exists for the CPU and the ceiling,
    "tier" for the curated fallback, and (None, None) for an unknown CPU.
    """
    cpu = CPU_CATALOG.get(cpu_key or "")
    if not cpu:
        return None, None
    ceiling = CPU_CATALOG.get(ceiling_key or BENCHMARK_CEILING_CPU) or CPU_CATALOG[BENCHMARK_CEILING_CPU]
    if cpu.get("bench_mt") and cpu.get("bench_st") and ceiling.get("bench_mt") and ceiling.get("bench_st"):
        multi = min(cpu["bench_mt"] / ceiling["bench_mt"], 1.0)
        single = min(cpu["bench_st"] / ceiling["bench_st"], 1.0)
        return BENCHMARK_MT_SHARE * multi + (1 - BENCHMARK_MT_SHARE) * single, "benchmark"
    if cpu_key in CPU_PERFORMANCE_POINTS:
        return CPU_PERFORMANCE_POINTS[cpu_key] / 50, "tier"
    return None, None


def short_cpu_name(name: str) -> str:
    """"Intel Core i5-12500T" -> "i5-12500T", "Intel Processor N100" -> "N100"."""
    return re.sub(r"^(?:Intel\s+(?:Core\s+|Processor\s+|Pentium\s+Gold\s+|Pentium\s+|Celeron\s+)?|AMD\s+)", "", name)


def _resolve_cpu_key(value: Any) -> str | None:
    """Accept a catalog key ("i512500t") or a CPU name ("i5-12500T", "Intel N100")."""
    text = str(value or "").strip()
    if text.lower() in CPU_CATALOG:
        return text.lower()
    name, _cpu = _canonical_cpu(text)
    return _cpu_key_for_name(name)


def reference_cpus(keys: Any = None) -> list[dict[str, Any]]:
    """Return reference CPUs with their benchmark data; unknown entries are skipped."""
    if not isinstance(keys, (list, tuple)):
        keys = DEFAULT_REFERENCE_CPUS
    references = []
    for value in keys:
        key = _resolve_cpu_key(value)
        if not key or any(ref["key"] == key for ref in references):
            if not key:
                logging.warning("Unknown reference CPU ignored: %r", value)
            continue
        cpu = CPU_CATALOG[key]
        references.append({
            "key": key, "name": short_cpu_name(cpu["name"]),
            "st": cpu.get("bench_st"), "mt": cpu.get("bench_mt"),
        })
    return references


def benchmark_for_name(cpu_name: str | None) -> dict[str, Any] | None:
    """Return {"st", "mt", "source"} for a canonical CPU name, or None."""
    cpu = CPU_CATALOG.get(_cpu_key_for_name(cpu_name) or "")
    if not cpu or not (cpu.get("bench_st") or cpu.get("bench_mt")):
        return None
    return {"st": cpu.get("bench_st"), "mt": cpu.get("bench_mt"),
            "source": f"{BENCHMARK_SOURCE} ({BENCHMARK_VERSION})"}


def compare_to_references(
    benchmark: dict[str, Any] | None, references: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Multithread/single-thread ratios against each reference; None when incomplete."""
    rows = []
    for ref in references:
        def ratio(metric: str) -> float | None:
            mine = (benchmark or {}).get(metric)
            return round(mine / ref[metric], 2) if mine and ref.get(metric) else None
        mt_ratio, st_ratio = ratio("mt"), ratio("st")
        rows.append({"name": ref["name"], "mt_ratio": mt_ratio, "st_ratio": st_ratio,
                     "complete": mt_ratio is not None and st_ratio is not None})
    return rows


def _find_platform_profile(text: str) -> dict[str, Any] | None:
    for profile in PLATFORM_PROFILES:
        if profile["pattern"].search(text):
            return profile
    return None


def _vpro_status(platform: dict[str, Any] | None, cpu_key: str | None) -> tuple[str, int]:
    """Return a remote-management label and its 0-6 score contribution."""
    if not platform:
        return "unknown: platform not identified", 0
    chipset = platform["vpro"]
    if chipset == "amd":
        return "no Intel AMT (AMD platform; DASH at most)", 0
    if chipset == "no":
        return "no: chipset without vPro/AMT", 0
    if chipset == "verify":
        return "to verify for this model", 2
    if cpu_key in VPRO_CPU_KEYS:
        return "likely: vPro chipset and vPro CPU (check AMT is enabled in BIOS)", 6
    if cpu_key in NON_VPRO_CPU_KEYS:
        return "no: vPro chipset, but this CPU lacks vPro", 1
    return "possible: vPro chipset, the CPU must be a vPro model", 4


def _cpu_candidates(text: str):
    """Yield (name, spec) for every catalog CPU mentioned, most specific patterns first."""
    n100 = N100_RE.search(text)
    if n100:
        yield "Intel Processor N100", CPU_CATALOG["n100"]

    for match in INTEL_CPU_RE.finditer(text):
        key = ("i" + match.group(1) + match.group(2) + (match.group(3) or "")).lower()
        # A suffixless model is not the same SKU as its low-power T version.
        if key in CPU_CATALOG:
            yield CPU_CATALOG[key]["name"], CPU_CATALOG[key]

    for match in AMD_CPU_RE.finditer(text):
        family = "ryzen" + match.group(1) + ("pro" if match.group(2) else "") + match.group(3)
        key = re.sub(r"[^a-z0-9]", "", family.lower())
        if key in CPU_CATALOG:
            yield CPU_CATALOG[key]["name"], CPU_CATALOG[key]

    for match in ENTRY_LEVEL_CPU_RE.finditer(text):
        key = re.sub(r"[^a-z0-9]", "", match.group(1).lower())
        if key in CPU_CATALOG:
            yield CPU_CATALOG[key]["name"], CPU_CATALOG[key]

    for match in AMD_A_SERIES_RE.finditer(text):
        key = f"a{match.group(1)}pro{match.group(2).lower()}"
        if key in CPU_CATALOG:
            yield CPU_CATALOG[key]["name"], CPU_CATALOG[key]

    for match in BARE_T_CPU_RE.finditer(text):
        number = match.group(1)
        keys = [f"i{tier}{number}t" for tier in "3579" if f"i{tier}{number}t" in CPU_CATALOG]
        # Accept the bare number only when it maps to exactly one Core model.
        if len(keys) == 1:
            yield CPU_CATALOG[keys[0]]["name"], CPU_CATALOG[keys[0]]



def _canonical_cpu(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """Return the first (most specific) catalog CPU mentioned in the text."""
    return next(_cpu_candidates(text), (None, None))


def cpu_mentions(text: str) -> list[str]:
    """Distinct catalog CPU names mentioned, in match-priority order."""
    names: list[str] = []
    for name, _cpu in _cpu_candidates(text):
        if name not in names:
            names.append(name)
    return names


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
            key = (attr.get("property") or attr.get("name") or attr.get("itemprop") or "").lower()
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


def _strip_title_prefix(text: str, title: str) -> str:
    """Remove the "<listing title> - " prefix Vinted adds to meta descriptions."""
    title = re.sub(r"\s+", " ", title or "").strip()
    if title and text.startswith(title):
        remainder = text[len(title):].lstrip()
        if remainder.startswith(("-", "–", "—")):
            return remainder[1:].strip()
    return text


class ListingRateLimited(Exception):
    """Raised when Vinted answers a listing-page request with HTTP 429."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"listing page rate-limited (retry_after={retry_after})")
        self.retry_after = retry_after


def _retry_after_seconds(response: Any) -> float | None:
    try:
        return float(response.headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None


def fetch_listing_description(
    item_url: str,
    session: Any = None,
    headers: dict[str, str] | None = None,
    title: str = "",
    raise_on_rate_limit: bool = False,
    on_error: Any = None,
) -> str:
    """Try to obtain listing text from the item's page; failure is non-fatal.

    Uses one bounded, normal page request for a newly detected listing, with the
    same browser-like headers the scanner already uses to open the marketplace.
    It does not use proxies, rotate identities, or try to evade access controls.
    ``on_error`` is called with the exception when the page cannot be read.
    """
    if not item_url:
        return ""
    request_options: dict[str, Any] = {"timeout": REQUEST_TIMEOUT_SECONDS}
    if headers:
        request_options["headers"] = headers
    try:
        if session is None:
            import requests
            response = requests.get(item_url, **request_options)
        else:
            response = session.get(item_url, **request_options)
        if raise_on_rate_limit and response.status_code == 429:
            raise ListingRateLimited(_retry_after_seconds(response))
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
            text = _strip_title_prefix(text, title)
            low = text.lower()
            if not text or low in {"vinted", ""}:
                continue
            if "compra e vendi" in low and len(text) < 220:
                continue
            if len(text) > 20 and text not in filtered:
                filtered.append(text)
        if not filtered:
            return ""
        return max(filtered, key=len)[:MAX_ANALYSIS_TEXT_CHARS]
    except ListingRateLimited:
        raise
    except Exception as error:  # detail enrichment must never stop the base notifier
        logging.warning("Could not fetch listing description (%s): %s", item_url, error)
        if on_error is not None:
            on_error(error)
        return ""


def _find_ram_gb(text: str) -> int | None:
    # Prefer number-first phrasing: "16GB RAM". Do this before "RAM 512GB"
    # because in "16GB RAM 512GB NVMe" the latter sequence would misread SSD size as RAM.
    explicit_after = re.search(r"\b(\d{1,3})\s*(?:gb|go)\s*(?:ddr[345]\s*)?(?:ram|memory|di ram|de ram)\b", text, re.I)
    if explicit_after:
        return int(explicit_after.group(1))
    explicit = re.search(r"\b(?:ram|memory|memoria|m[eé]moire)\s*[:=]\s*(\d{1,3})\s*(?:gb|go)\b", text, re.I)
    if explicit:
        return int(explicit.group(1))
    explicit = re.search(r"\b(?:ram|memory|memoria|m[eé]moire)\s+(\d{1,3})\s*(?:gb|go)\b", text, re.I)
    if explicit:
        return int(explicit.group(1))

    # Common resale title pattern: "i5-8600T | 16GB | 256GB SSD". Treat as
    # RAM the first capacity that is not attached to an explicit storage label.
    storage_spans = [m.span() for m in re.finditer(
        r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb|go|to)\s*(?:nvme|ssd|hdd|sshd|sata|m\.2|hard\s?disk|hard\s?drive|emmc)",
        text, re.I
    )]
    capacity_matches = list(re.finditer(r"\b(\d{1,4})\s*(?:gb|go)\b", text, re.I))
    for match in capacity_matches:
        # Mini-PC RAM sizes; larger numbers in "256go/8go" style titles are storage.
        if int(match.group(1)) not in PLAUSIBLE_RAM_SIZES_GB:
            continue
        if not any(start <= match.start() < end for start, end in storage_spans):
            # If the capacity appears after a word like SSD/HDD, ignore it.
            before = text[max(0, match.start() - 14):match.start()].lower()
            if re.search(r"(?:ssd|hdd|sshd|nvme|storage|archiviazione)[\s:=]*$", before):
                continue
            return int(match.group(1))
    return None


RAM_LAYOUT_RE = re.compile(r"\b([1-4])\s*[x×*]\s*(\d{1,2})\s*(?:gb|go)\b", re.I)
RAM_PAIR_RE = re.compile(r"\b(\d{1,2})\s*(?:gb|go)?\s*\+\s*(\d{1,2})\s*(?:gb|go)\b", re.I)
SINGLE_MODULE_RE = re.compile(
    r"\b(?:single\s+(?:module|stick|dimm|so-?dimm)|(?:un\s+)?(?:unico|singolo)\s+(?:modulo|banco)"
    r"|(?:modulo|banco)\s+(?:unico|singolo)|une?\s+seule?\s+barrette)\b",
    re.I,
)


def _find_ram_layout(text: str, ram_gb: int | None) -> list[int] | None:
    """Return installed module sizes, e.g. [8, 8], only when the listing states them.

    The layout must add up to the installed RAM; anything else stays unknown,
    so 16 GB is never assumed to be 1×16 or 2×8.
    """
    candidates: list[list[int]] = []
    for match in RAM_LAYOUT_RE.finditer(text):
        count, size = int(match.group(1)), int(match.group(2))
        if size in RAM_MODULE_SIZES_GB:
            candidates.append([size] * count)
    for match in RAM_PAIR_RE.finditer(text):
        sizes = [int(match.group(1)), int(match.group(2))]
        if all(size in RAM_MODULE_SIZES_GB for size in sizes):
            candidates.append(sorted(sizes, reverse=True))
    if ram_gb and SINGLE_MODULE_RE.search(text):
        candidates.append([ram_gb])
    for modules in candidates:
        if ram_gb is None or sum(modules) == ram_gb:
            return modules
    return None


def ram_layout_label(modules: list[int] | None) -> str:
    """[8, 8] -> "2×8 GB", [16, 8] -> "16+8 GB", None -> "layout unknown"."""
    if not modules:
        return "layout unknown"
    if len(set(modules)) == 1:
        return f"{len(modules)}×{modules[0]} GB"
    return "+".join(str(size) for size in modules) + " GB"


def _storage_unit(unit: str) -> str:
    """Normalize GB/TB and the French Go/To spelling."""
    return {"GO": "GB", "TO": "TB"}.get(unit.upper(), unit.upper())


def _find_storage(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    # Capacity first: 512 GB NVMe SSD, 500GB SSHD.
    pattern_a = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|go|to)\s*(nvme(?:\s+ssd)?|ssd|hdd|sshd|sata\s+ssd|m\.2(?:\s+ssd)?)\b", re.I)
    for match in pattern_a.finditer(text):
        capacity = match.group(1).replace(",", ".")
        unit = _storage_unit(match.group(2))
        kind = re.sub(r"\s+", " ", match.group(3)).upper()
        found.append((match.start(), f"{capacity} {unit} {kind}"))
    # Reverse order: NVMe 512GB / SSD da 512 GB.
    pattern_b = re.compile(r"\b(nvme|ssd|hdd|sshd|sata\s+ssd)\s*(?:da|of|de)?\s*(\d+(?:[.,]\d+)?)\s*(gb|tb|go|to)\b", re.I)
    for match in pattern_b.finditer(text):
        capacity = match.group(2).replace(",", ".")
        found.append((match.start(), f"{capacity} {_storage_unit(match.group(3))} {match.group(1).upper()}"))
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


def _find_platform_upgrade_profile(text: str) -> dict[str, Any] | None:
    for profile in PLATFORM_UPGRADE_PROFILES:
        if profile["pattern"].search(text):
            cpu = CPU_CATALOG.get(profile["candidate_key"])
            if not cpu:
                continue
            return {
                "platform": profile["platform"],
                "candidate_cpu": cpu["name"],
                "candidate_key": profile["candidate_key"],
                "cores": cpu["cores"],
                "threads": cpu["threads"],
                "tdp_w": cpu.get("tdp_w"),
                "reference": profile["reference"],
                "source_url": profile["source_url"],
            }
    return None


def _cpu_key_for_name(cpu_name: str | None) -> str | None:
    if not cpu_name:
        return None
    for key, specification in CPU_CATALOG.items():
        if specification.get("name", "").casefold() == cpu_name.casefold():
            return key
    return None


def _cpu_upgrade_estimate(
    profile: dict[str, Any] | None,
    current_cpu_name: str | None,
    configured_costs: dict[str, Any] | None,
) -> dict[str, Any]:
    if not profile:
        return {
            "candidate_cpu": None, "candidate_cores": None, "candidate_threads": None,
            "candidate_tdp_w": None, "needed": None, "cost_eur": None,
            "reference": None, "source_url": None,
        }

    current_key = _cpu_key_for_name(current_cpu_name)
    current_points = cpu_performance(current_key)[0] or 0.0
    candidate_points = cpu_performance(profile["candidate_key"])[0] or 0.0
    needed = not current_key or current_points < candidate_points

    costs = configured_costs if isinstance(configured_costs, dict) else {}
    configured_cost = costs.get(profile["candidate_key"])
    if configured_cost is None:
        configured_cost = costs.get(profile["candidate_cpu"].casefold())
    try:
        cost_eur = float(configured_cost) if configured_cost is not None else None
        if cost_eur is not None and cost_eur < 0:
            cost_eur = None
    except (TypeError, ValueError):
        cost_eur = None

    if not needed:
        cost_eur = 0.0

    return {
        "candidate_cpu": profile["candidate_cpu"],
        "candidate_cores": profile["cores"],
        "candidate_threads": profile["threads"],
        "candidate_tdp_w": profile["tdp_w"],
        "needed": needed,
        "cost_eur": cost_eur,
        "reference": profile["reference"],
        "source_url": profile["source_url"],
    }


# Bump whenever target/exclusion rules change, so listings filtered by an older
# rule set are evaluated again instead of staying hidden forever.
FILTER_RULES_VERSION = "2"

# Applied to the listing title only: descriptions of complete systems often
# mention fans, power adapters or brackets ("alimentatore incluso").
# Parts and lots are rejected outright.
PARTS_OR_LOT_TITLE_RE = re.compile(
    r"\b(?:ricambi(?:o)?|pezzi\s+di\s+ricambio|pi[eè]ces?\s+d[eé]tach[eé]es|"
    r"spare\s+parts?|for\s+parts|ersatzteile?|repuestos?|"
    r"lot(?:to)?\s+(?:de|di|of|da)\s+\d+|"
    r"\d+\s*(?:x|pz|pezzi|pcs|st[uü]ck)\s+(?:mini\s*pc|lenovo|hp|dell|thinkcentre|elitedesk|prodesk|optiplex)|"
    r"stock\s+(?:di|de|of)\b)",
    re.I,
)
# Component names reject a title only when it names no CPU and no RAM size, so
# "M920q i5-8500T 16GB con antenna WiFi" is still treated as a system.
COMPONENT_TITLE_RE = re.compile(
    r"\b(?:staffa|staffe|antenna|antenne|ventola|ventole|dissipatore|heatsink|"
    r"scheda\s+madre|motherboard|mainboard|scheda\s+wi-?fi|halterung|l[uü]fter|"
    r"k[uü]hler|ventilateur|supporto\s+vesa|vesa\s+mount|bracket|caddy|"
    r"solo\s+case|case\s+vuoto)\b"
    r"|^\s*(?:alimentatore|caricatore|caricabatterie|adattatore|power\s+supply|"
    r"charger|chargeur|netzteil|bloc\s+d'alimentation|cavo|c[aâ]ble)\b",
    re.I,
)
SYSTEM_EVIDENCE_RE = re.compile(
    r"\b(?:i[3579]|celeron|pentium|ryzen|core)\b|\b\d{1,2}\s*(?:gb|go)\b", re.I
)


def is_non_system_title(title: str) -> bool:
    """Return True for spare parts, accessories and multi-unit lots."""
    title = title or ""
    if PARTS_OR_LOT_TITLE_RE.search(title):
        return True
    return bool(COMPONENT_TITLE_RE.search(title)) and not SYSTEM_EVIDENCE_RE.search(title)


TARGET_FAMILY_RE = re.compile(
    r"\b(?:thinkcentre|elitedesk|prodesk|optiplex)\b|\blenovo\s+m\d{2,4}q?\b", re.I
)


def _is_target_system(text: str) -> tuple[bool, str | None]:
    """Accept the requested HP Mini, Lenovo Tiny and Dell Micro chassis.

    Lenovo's M700 Tiny is sometimes advertised simply as M700 USFF. PSREF lists
    its actual chassis as Tiny (1L); accept that exact model when USFF is stated.
    This exception does not open the filter to arbitrary SFF/USFF desktops.
    """
    checks = [
        (r"\b(?:HP\s+)?EliteDesk\s+(?:(?:\d{3,4})(?:\s+G\d)?\s+)?(?:Mini|DM|Desktop\s+Mini)\b", "HP EliteDesk Mini"),
        (r"\b(?:HP\s+)?ProDesk\s+(?:(?:\d{3,4})(?:\s+G\d)?\s+)?(?:Mini|DM|Desktop\s+Mini)\b", "HP ProDesk Mini"),
        (r"\b(?:Lenovo\s+)?ThinkCentre\s+M\d{2,4}q\b", "Lenovo ThinkCentre Tiny"),
        (r"\b(?:Lenovo\s+)?ThinkCentre\s+M\d{3,4}\s+Tiny\b", "Lenovo ThinkCentre Tiny"),
        (r"\b(?:Lenovo\s+)?ThinkCentre\s+M700\b(?=[\s\S]{0,180}\b(?:USFF|Ultra\s+Small\s+Form\s+Factor|Tiny)\b)", "Lenovo ThinkCentre Tiny (M700 / USFF wording)"),
        (r"\b(?:Dell\s+)?OptiPlex\s+\d{4}\s+Micro(?:\s+Form\s+Factor)?\b", "Dell OptiPlex Micro"),
    ]
    for pattern, family in checks:
        if re.search(pattern, text, re.I):
            return True, family
    return False, None


def is_target_system(title: str) -> tuple[bool, str | None]:
    """Public title-only check, used by the scanner before any detail fetch."""
    if is_non_system_title(title):
        return False, None
    return _is_target_system(title or "")


def names_target_family(text: str) -> bool:
    """Return True when text names a supported family without proving the chassis.

    Such listings (e.g. "Lenovo ThinkCentre M700" or "HP EliteDesk 800 G3")
    may confirm the Tiny/Mini/Micro form factor only in the description.
    """
    return bool(TARGET_FAMILY_RE.search(text or ""))


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
    ram_modules: list[int] | None = None,
    ram_16gb_module_eur: float = DEFAULT_RAM_16GB_MODULE_EUR,
) -> dict[str, Any]:
    """Estimate outlay for 16/32 GB without assuming unknown RAM topology.

    Supported Mini/Tiny/Micro chassis have two SO-DIMM slots. A stated layout
    refines the estimate; an unknown layout keeps the conservative budget.
    """
    listing_price = _parse_price_eur(price)
    upgrade_16: float | None
    upgrade_32: float | None
    note = None

    if ram_gb is None:
        upgrade_16 = None
        upgrade_32 = None
        note = "RAM not stated: upgrade cost to be verified"
    else:
        if ram_gb >= 16:
            upgrade_16 = 0.0
        elif ram_gb == 8 and ram_modules == [4, 4]:
            # Both slots are used: reaching 16 GB means replacing the modules.
            upgrade_16 = float(ram_8gb_upgrade_eur)
            note = "16 GB estimate is a rough budget: both slots used (2×4 GB), modules must be replaced"
        elif ram_gb == 8:
            # User-supplied assumption: add one compatible 8 GB module.
            upgrade_16 = float(ram_8gb_upgrade_eur)
            if ram_modules == [8]:
                note = "16 GB estimate: 1×8 GB installed, add one compatible 8 GB module"
            else:
                note = "16 GB estimate: assumes adding 1×8 GB compatible module"
        elif ram_gb < 8:
            # A 4 GB base may need a replacement module or a kit; keep the same
            # planning allowance, but mark it as a rough estimate, not a fit claim.
            upgrade_16 = float(ram_8gb_upgrade_eur)
            note = "16 GB estimate is a rough budget: with less than 8 GB installed, check kit, slots and compatibility"
        else:
            upgrade_16 = float(ram_8gb_upgrade_eur)
            note = "16 GB estimate is a rough budget: check module layout and compatibility"
        if ram_gb >= 32:
            upgrade_32 = 0.0
        elif ram_modules == [16]:
            # One 16 GB module in a two-slot chassis: add a matching 16 GB module.
            upgrade_32 = float(ram_16gb_module_eur)
            if ram_gb >= 16:
                note = "32 GB estimate: 1×16 GB installed, add one compatible 16 GB module"
        else:
            upgrade_32 = float(ram_32gb_kit_eur)
            if ram_gb >= 16 and ram_modules and len(ram_modules) >= 2:
                note = f"32 GB estimate: {ram_layout_label(ram_modules)} installed, both modules replaced by a 2×16 GB kit"

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
    cpu_upgrade_costs_eur: dict[str, Any] | None = None,
    reference_cpu_keys: Any = None,
    ram_16gb_module_eur: float = DEFAULT_RAM_16GB_MODULE_EUR,
    score_weights: Any = None,
) -> dict[str, Any]:
    """Extract a small structured hardware profile and an explainable ranking."""
    weights = normalize_score_weights(score_weights)
    title = title or ""
    description = description or ""
    text = f"{title}\n{description}"
    cpu_name, cpu = _canonical_cpu(text)
    mentioned_cpus = cpu_mentions(text)
    ram_gb = _find_ram_gb(text)
    ram_modules = _find_ram_layout(text, ram_gb)
    storage = _find_storage(text)
    model = _find_model(title) or _find_model(text)
    target_system, target_family = _is_target_system(text)
    non_system = is_non_system_title(title)
    if non_system:
        target_system, target_family = False, None
    platform = _find_platform_profile(text) if target_system else None

    cpu_key = _cpu_key_for_name(cpu_name)
    cpu_note = None
    if platform and platform["max_tdp_w"] == 35 and cpu and (cpu.get("tdp_w") or 0) > 35:
        # Intel T and AMD GE are the low-power variants of the listed part.
        t_key = next(
            (f"{cpu_key}{suffix}" for suffix in ("t", "e") if f"{cpu_key}{suffix}" in CPU_CATALOG),
            None,
        )
        if t_key:
            cpu_note = (
                f"35 W chassis: listed as {cpu_name}, probably the {CPU_CATALOG[t_key]['name']}; verify"
            )
            cpu_key, cpu = t_key, CPU_CATALOG[t_key]
            cpu_name = cpu["name"]

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
            cpu_detail += f" · nominal TDP {cpu['tdp_w']} W"
        if cpu.get("architecture"):
            cpu_detail += f" · {cpu['architecture']}, {cpu['launch_year']}"
    else:
        cpu_detail = "Model not recognized with enough certainty"

    pricing = _upgrade_estimates(
        ram_gb, price, ram_8gb_upgrade_eur, ram_32gb_kit_eur,
        ram_modules=ram_modules, ram_16gb_module_eur=ram_16gb_module_eur,
    )
    platform_upgrade = _find_platform_upgrade_profile(text) if target_system else None
    cpu_upgrade = _cpu_upgrade_estimate(platform_upgrade, cpu_name, cpu_upgrade_costs_eur)
    listing_price = pricing.get("listing_price_eur")
    cpu_cost = cpu_upgrade.get("cost_eur")
    cpu_required = cpu_upgrade.get("needed")
    for target_key in ("total_to_16_eur", "total_to_32_eur"):
        combined_key = target_key.replace("_eur", "_with_cpu_eur")
        base_total = pricing.get(target_key)
        if base_total is not None and cpu_required is False:
            pricing[combined_key] = base_total
        elif base_total is not None and cpu_required is True and cpu_cost is not None:
            pricing[combined_key] = base_total + cpu_cost
        else:
            pricing[combined_key] = None

    score_parts = _score(
        cpu_name=cpu_name,
        ram_gb=ram_gb,
        storage=storage,
        platform=platform,
        target_system=target_system,
        pricing=pricing,
        estimated_cpu_key=None if cpu_name else _estimated_cpu_key(platform, text),
        weights=weights,
    )
    score = score_parts["total"]

    upgrade_score_parts = None
    upgrade_score = None
    if platform_upgrade and (cpu_required is False or cpu_cost is not None):
        upgrade_cpu_name = (
            cpu_upgrade.get("candidate_cpu") if cpu_required else cpu_name
        ) or cpu_name
        upgrade_total = (
            pricing.get("total_to_16_with_cpu_eur")
            if cpu_required else pricing.get("total_to_16_eur")
        )
        if upgrade_total is not None:
            upgrade_score_parts = _score(
                cpu_name=upgrade_cpu_name,
                ram_gb=ram_gb,
                storage=storage,
                platform=platform,
                target_system=target_system,
                pricing={"effective_price_override": upgrade_total},
                weights=weights,
            )
            upgrade_score = upgrade_score_parts["total"]
    if non_system:
        verdict = "Out of target: spare part, accessory or lot"
    elif not target_system:
        verdict = "Out of target: requires EliteDesk/ProDesk Mini, ThinkCentre Tiny or OptiPlex Micro"
    elif score >= STRONG_CANDIDATE_SCORE and not (
        platform and (platform["generation"] or 99) <= 7
    ):
        verdict = "Strong candidate"
    elif score >= WORTH_EVALUATING_SCORE:
        verdict = "Worth evaluating"
    elif cpu_upgrade.get("needed") and cpu_upgrade.get("candidate_cores"):
        if known_cores is not None and cpu_upgrade["candidate_cores"] > known_cores:
            verdict = "Cheap base, potentially upgradeable"
        else:
            verdict = "Weak as configured; upgrade to be verified"
    else:
        verdict = "Weak for a Proxmox node"

    missing = []
    if non_system:
        missing.append("out of target: spare part, accessory or multi-unit lot")
    elif not target_system:
        missing.append("out of target: family/chassis not confirmed as the required Mini/Tiny/Micro")
    if not cpu_name:
        missing.append("exact CPU/core count to be verified")
    if ram_gb is None:
        missing.append("installed RAM to be verified")
    if not storage:
        missing.append("storage type and capacity to be verified")
    if not form_factor:
        missing.append("Mini/Tiny/Micro form factor to be confirmed")
    missing.append("check maximum RAM, free slots, power adapter and actual condition")
    missing.append("check storage expansion and compatibility of any upgrade")
    if pricing.get("ram_upgrade_note"):
        missing.append(pricing["ram_upgrade_note"])

    vpro_label, _vpro_points = _vpro_status(platform, cpu_key)
    platform_info = None
    legacy_note = None
    if platform:
        platform_info = {
            "name": platform["name"],
            "generation": platform["generation"],
            "max_ram_gb": platform["max_ram_gb"],
            "pcie_slot": platform["pcie_slot"],
            "max_tdp_w": platform["max_tdp_w"],
            "vpro": vpro_label,
        }
        if platform["generation"] is not None and platform["generation"] <= 7:
            legacy_note = (
                f"Legacy platform ({platform['generation']}th gen): max "
                f"{platform['max_ram_gb']} GB RAM and 4 cores; the CPU upgrade path is informational"
            )
    if cpu_note:
        missing.insert(0, cpu_note)
    if len(mentioned_cpus) > 1:
        missing.insert(0, "multiple CPUs mentioned (" + ", ".join(short_cpu_name(n) for n in mentioned_cpus)
                       + f"); analysis uses {short_cpu_name(mentioned_cpus[0])}, verify which is installed")

    ai_note = _ai_note(gpu, bool(discrete_match), cpu_name)
    cpu_benchmark = benchmark_for_name(cpu_name)
    cpu_vs_reference = (
        compare_to_references(cpu_benchmark, reference_cpus(reference_cpu_keys)) if cpu_name else []
    )
    return {
        "model": model,
        "cpu": cpu_name,
        "cpu_detail": cpu_detail,
        "cpu_candidates": mentioned_cpus if len(mentioned_cpus) > 1 else [],
        "cpu_architecture": cpu.get("architecture") if cpu else None,
        "cpu_launch_year": cpu.get("launch_year") if cpu else None,
        "cpu_benchmark": cpu_benchmark,
        "cpu_vs_reference": cpu_vs_reference,
        "cores": known_cores,
        "threads": known_threads,
        "ram_gb": ram_gb,
        "ram_modules": ram_modules,
        "storage": storage,
        "gpu": gpu,
        "form_factor": form_factor,
        "target_system": target_system,
        "target_family": target_family,
        "non_system": non_system,
        "platform": platform_info,
        "remote_management": vpro_label,
        "legacy_note": legacy_note,
        "cpu_note": cpu_note,
        "cpu_estimated": score_parts.get("cpu_estimated", False),
        "score": score,
        "score_parts": score_parts,
        "upgrade_score": upgrade_score,
        "upgrade_score_parts": upgrade_score_parts,
        "cpu_upgrade": cpu_upgrade,
        "verdict": verdict,
        "missing": missing,
        "ai_note": ai_note,
        "price": price,
        **pricing,
        "description": re.sub(r"\s+", " ", description).strip()[:MAX_DESCRIPTION_CHARS],
        "spec_source": "local reference specs + listing text; not a device test",
    }


def normalize_score_weights(weights: Any = None) -> dict[str, float]:
    """Return component maxima summing to 100; invalid configuration means defaults."""
    if weights is None:
        return dict(DEFAULT_SCORE_WEIGHTS)
    try:
        if not isinstance(weights, dict) or set(weights) - set(DEFAULT_SCORE_WEIGHTS):
            raise ValueError("unknown or malformed components")
        merged = {key: float(weights.get(key, default)) for key, default in DEFAULT_SCORE_WEIGHTS.items()}
        if any(value < 0 for value in merged.values()) or sum(merged.values()) <= 0:
            raise ValueError("weights must be non-negative with a positive total")
    except (TypeError, ValueError) as error:
        logging.warning("Invalid hardware_score_weights (%s); using defaults", error)
        return dict(DEFAULT_SCORE_WEIGHTS)
    total = sum(merged.values())
    return {key: value * 100 / total for key, value in merged.items()}


def _score(
    cpu_name: str | None,
    ram_gb: int | None,
    storage: list[str],
    platform: dict[str, Any] | None,
    target_system: bool,
    pricing: dict[str, Any],
    estimated_cpu_key: str | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Return 100 explainable points, by default CPU 40, price 25, RAM 10,
    storage 10, platform 15; ``weights`` (normalized) rescales each component."""
    cpu_key = _cpu_key_for_name(cpu_name)
    cpu_estimated = False
    fraction, cpu_source = cpu_performance(cpu_key)
    estimated_fraction, _source = cpu_performance(estimated_cpu_key)
    if cpu_name and fraction is not None:
        cpu_points = round(fraction * 50 * CPU_WEIGHT)
    elif cpu_name:
        cpu_points = round(10 * CPU_WEIGHT)
        cpu_source = "tier"
    elif estimated_fraction is not None:
        # Unknown CPU on a known platform: assume a typical part, with a discount.
        cpu_points = round(estimated_fraction * 50 * CPU_WEIGHT * ESTIMATED_CPU_FACTOR)
        cpu_estimated = True
        cpu_source = "estimated"
    else:
        cpu_points = UNKNOWN_CPU_POINTS
        cpu_source = "unknown"

    if "effective_price_override" in pricing:
        effective_price = pricing.get("effective_price_override")
    else:
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
        capacity_points = 5
    elif max_gb >= 512:
        capacity_points = 4
    elif max_gb >= 256:
        capacity_points = 3
    elif max_gb >= 128:
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
    storage_points = min(10, capacity_points + type_points + multi_drive_points)

    _label, vpro_points = _vpro_status(platform, cpu_key)
    if platform is None:
        generation_points = 0
    elif platform["generation"] is None:
        generation_points = 2
    elif platform["generation"] >= 8:
        generation_points = 5
    else:
        generation_points = 0
    pcie_points = 2 if platform and platform["pcie_slot"] else 0
    cpu_tdp = CPU_CATALOG.get(cpu_key or "", {}).get("tdp_w")
    low_power_points = 2 if cpu_tdp is not None and cpu_tdp <= 35 else 0
    platform_points = min(15, vpro_points + generation_points + pcie_points + low_power_points)

    maxima = weights or DEFAULT_SCORE_WEIGHTS
    raw = {"cpu": cpu_points, "price": price_points, "ram": ram_points,
           "storage": storage_points, "platform": platform_points}
    # Each component is computed on its default scale, then rescaled.
    parts = {key: round(raw[key] * maxima[key] / DEFAULT_SCORE_WEIGHTS[key]) for key in raw}
    total = sum(parts.values())
    return {
        **parts,
        "cpu_estimated": cpu_estimated,
        "cpu_source": cpu_source,
        "max": {key: round(value) for key, value in maxima.items()},
        "total": max(0, min(100, total)),
        "target_system": target_system,
    }


def _ai_note(gpu: str | None, discrete_gpu: bool, cpu_name: str | None) -> str:
    if discrete_gpu:
        return f"Dedicated GPU mentioned ({gpu}); check VRAM and chassis/power compatibility."
    if gpu and re.search(r"Radeon\s+(?:680M|780M|890M|880M)", gpu, re.I):
        return "Fairly capable iGPU for light experiments; not equivalent to a dedicated GPU with VRAM."
    if gpu and re.search(r"(?:UHD|HD Graphics|N100)", gpu, re.I):
        return "Basic integrated graphics; local AI mostly on CPU with small models."
    if cpu_name:
        return "No dedicated GPU confirmed; for local AI the exact model and RAM matter most."
    return "Not enough data to estimate AI acceleration; GPU and VRAM not confirmed."


def enrich_item(
    item: dict[str, Any],
    session: Any = None,
    fetch_details: bool = True,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Attach hardware_analysis to a normalized scanner item, non-fatally."""
    description = str(item.get("description") or "").strip()
    if fetch_details and not description:
        description = fetch_listing_description(
            str(item.get("url") or ""), session=session, headers=headers,
            title=str(item.get("title") or ""),
        )
    try:
        import Config
        ram_8gb_upgrade_eur = float(getattr(Config, "hardware_ram_8gb_upgrade_eur", DEFAULT_RAM_8GB_UPGRADE_EUR))
        ram_32gb_kit_eur = float(getattr(Config, "hardware_ram_32gb_kit_eur", DEFAULT_RAM_32GB_KIT_EUR))
        cpu_upgrade_costs_eur = getattr(Config, "hardware_cpu_upgrade_costs_eur", {})
        reference_cpu_keys = getattr(Config, "hardware_reference_cpus", DEFAULT_REFERENCE_CPUS)
        ram_16gb_module_eur = float(getattr(Config, "hardware_ram_16gb_module_eur", DEFAULT_RAM_16GB_MODULE_EUR))
        score_weights = getattr(Config, "hardware_score_weights", None)
    except (ImportError, TypeError, ValueError):
        ram_8gb_upgrade_eur = DEFAULT_RAM_8GB_UPGRADE_EUR
        ram_32gb_kit_eur = DEFAULT_RAM_32GB_KIT_EUR
        cpu_upgrade_costs_eur = {}
        reference_cpu_keys = DEFAULT_REFERENCE_CPUS
        ram_16gb_module_eur = DEFAULT_RAM_16GB_MODULE_EUR
        score_weights = None
    analysis = analyze_hardware(
        str(item.get("title") or ""),
        description,
        str(item.get("price") or ""),
        ram_8gb_upgrade_eur=ram_8gb_upgrade_eur,
        ram_32gb_kit_eur=ram_32gb_kit_eur,
        cpu_upgrade_costs_eur=cpu_upgrade_costs_eur,
        reference_cpu_keys=reference_cpu_keys,
        ram_16gb_module_eur=ram_16gb_module_eur,
        score_weights=score_weights,
    )
    item["description"] = description
    item["hardware_analysis"] = analysis
    return item


VERDICT_MARKERS = {
    "Strong candidate": "🟢",
    "Worth evaluating": "🟡",
    "Cheap base, potentially upgradeable": "🟠",
    "Weak as configured; upgrade to be verified": "🟠",
    "Weak for a Proxmox node": "🔴",
}


def _analysis_for(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("hardware_analysis") or analyze_hardware(
        str(item.get("title") or ""), str(item.get("description") or ""), str(item.get("price") or "")
    )


def build_telegram_headline(item: dict[str, Any]) -> str:
    """Return the decision line shown above the listing title (Telegram HTML)."""
    analysis = _analysis_for(item)
    verdict = str(analysis.get("verdict") or "")
    marker = VERDICT_MARKERS.get(verdict, "⚪")
    headline = f"{marker} <b>{analysis.get('score', 0)}/100</b> · {fmt.esc(verdict)}"
    if analysis.get("upgrade_score") is not None:
        headline += f" · ⬆️ {analysis['upgrade_score']}/100 after upgrade"
    return headline


def _benchmark_row(analysis: dict[str, Any]) -> str:
    """"Bench: ST 2,105 · MT 8,008 · ×1.5 N100 · ×0.49 i5-12500T" or "Bench: N/A"."""
    benchmark = analysis.get("cpu_benchmark")
    if not benchmark:
        return "Bench: N/A (no benchmark data for this CPU)"
    def number(value: Any) -> str:
        return f"{value:,}" if value else "N/A"
    parts = [f"ST {number(benchmark.get('st'))}", f"MT <b>{number(benchmark.get('mt'))}</b>"]
    for ref in analysis.get("cpu_vs_reference") or []:
        if ref.get("mt_ratio") is None:
            parts.append(f"vs {fmt.esc(ref['name'])} incomplete")
        else:
            parts.append(f"×{ref['mt_ratio']:.1f} {fmt.esc(ref['name'])}")
    return "Bench: " + " · ".join(parts)


def _hardware_rows(analysis: dict[str, Any]) -> list[str]:
    rows = []
    if analysis.get("cpu"):
        rows.append(f"CPU: <b>{fmt.esc(analysis['cpu'])}</b> · {fmt.esc(analysis.get('cpu_detail'))}")
    elif analysis.get("cpu_estimated") and analysis.get("platform"):
        rows.append("CPU: ❓ not identified · score assumes a typical CPU for this platform")
    else:
        rows.append("CPU: ❓ not identified")
    if analysis.get("cpu"):
        rows.append(_benchmark_row(analysis))
    if analysis.get("cpu_note"):
        rows.append(f"   {fmt.italic(analysis['cpu_note'])}")

    platform = analysis.get("platform")
    if analysis.get("ram_gb") is not None:
        ram = f"RAM: <b>{analysis['ram_gb']} GB</b> ({ram_layout_label(analysis.get('ram_modules'))})"
        if platform and platform.get("max_ram_gb"):
            ram += f" · max {platform['max_ram_gb']} GB"
        rows.append(ram)
    else:
        rows.append("RAM: ❓ not stated")
    storage = " + ".join(analysis["storage"]) if analysis.get("storage") else None
    rows.append(f"Disk: <b>{fmt.esc(storage)}</b>" if storage else "Disk: ❓ not stated")
    if analysis.get("gpu"):
        rows.append(f"GPU: {fmt.esc(analysis['gpu'])}")

    if platform:
        generation = f"{platform['generation']}th gen Intel" if platform.get("generation") else "AMD"
        pcie = {True: "PCIe slot", False: "no PCIe", None: "PCIe to verify"}[platform.get("pcie_slot")]
        tdp = f"CPU ≤{platform['max_tdp_w']} W" if platform.get("max_tdp_w") else "CPU TDP to verify"
        rows.append(f"Base: {fmt.esc(platform['name'])} · {generation} · {pcie} · {tdp}")
    else:
        model = analysis.get("model") or "model not identified"
        form = analysis.get("form_factor") or "form factor not confirmed"
        rows.append(f"Base: {fmt.esc(model)} · {fmt.esc(form)} · not in the platform table")
    remote = str(analysis.get("remote_management") or "")
    if remote and not remote.startswith("unknown"):
        rows.append(f"vPro/AMT: {fmt.esc(remote)}")
    if not analysis.get("target_system"):
        rows.append("⚠️ Chassis not confirmed as Mini/Tiny/Micro")
    return rows


def _cost_rows(analysis: dict[str, Any]) -> list[str]:
    def total(key: str, label: str, missing: str | None) -> str | None:
        if analysis.get(key) is not None:
            return f"{label}: <b>{fmt.euro(analysis[key])}</b>"
        return f"{label}: {missing}" if missing else None

    cpu_upgrade = analysis.get("cpu_upgrade") or {}
    with_cpu_missing = None
    if cpu_upgrade.get("needed") and cpu_upgrade.get("cost_eur") is None:
        with_cpu_missing = "incomplete — add a CPU price to hardware_cpu_upgrade_costs_eur"
    elif cpu_upgrade.get("needed"):
        with_cpu_missing = "incomplete — installed RAM unknown"
    rows = [
        total("total_to_16_eur", "With 16 GB", "RAM configuration to verify"),
        total("total_to_32_eur", "With 32 GB", "RAM configuration to verify"),
        total("total_to_16_with_cpu_eur", "16 GB + CPU upgrade", with_cpu_missing),
        total("total_to_32_with_cpu_eur", "32 GB + CPU upgrade", None),
    ]
    if analysis.get("ram_upgrade_note"):
        rows.append(f"   {fmt.italic(analysis['ram_upgrade_note'])}")
    return rows


def _upgrade_rows(analysis: dict[str, Any]) -> list[str]:
    cpu_upgrade = analysis.get("cpu_upgrade") or {}
    if not cpu_upgrade.get("candidate_cpu"):
        return []
    candidate = fmt.esc(cpu_upgrade["candidate_cpu"])
    rows = []
    if cpu_upgrade.get("needed"):
        detail = f"{cpu_upgrade['candidate_cores']} core / {cpu_upgrade['candidate_threads']} thread"
        if cpu_upgrade.get("candidate_tdp_w"):
            detail += f" · {cpu_upgrade['candidate_tdp_w']} W"
        rows.append(f"Candidate: <b>{candidate}</b> · {detail}")
        if cpu_upgrade.get("cost_eur") is not None:
            rows.append(f"CPU budget: {fmt.euro(cpu_upgrade['cost_eur'])}")
        else:
            rows.append("CPU budget: not configured — check current listings")
        if analysis.get("upgrade_score") is None:
            if cpu_upgrade.get("cost_eur") is None:
                rows.append("Upgrade score: pending — CPU upgrade price not configured")
            else:
                rows.append("Upgrade score: pending — installed RAM unknown, build cost not estimated")
    else:
        rows.append(f"Current CPU already at/above the listed candidate ({candidate})")
    if cpu_upgrade.get("reference"):
        rows.append(f"   {fmt.italic(cpu_upgrade['reference'])}")
    rows.append(f"   {fmt.italic('Verify exact MTM/BIOS/PSU and cooling before buying a CPU.')}")
    return rows


def _score_rows(analysis: dict[str, Any]) -> list[str]:
    parts = analysis.get("score_parts", {})
    estimated = parts.get("cpu_estimated")
    maxima = parts.get("max") or DEFAULT_SCORE_WEIGHTS
    breakdown = (
        f"CPU {parts.get('cpu', 0)}/{maxima['cpu']:.0f}{'*' if estimated else ''} · "
        f"Price {parts.get('price', 0)}/{maxima['price']:.0f} · RAM {parts.get('ram', 0)}/{maxima['ram']:.0f} · "
        f"Disk {parts.get('storage', 0)}/{maxima['storage']:.0f} · Platform {parts.get('platform', 0)}/{maxima['platform']:.0f}"
    )
    rows = [fmt.code(breakdown)]
    if estimated:
        rows.append(fmt.italic("* CPU estimated from the platform"))
    return rows


def build_telegram_lines(item: dict[str, Any], matched_query: str | None = None) -> list[str]:
    """Build the hardware body of a Telegram alert as Telegram HTML lines."""
    analysis = _analysis_for(item)
    lines = []
    lines += fmt.section("🖥 Hardware", _hardware_rows(analysis))
    lines += fmt.section("💰 Cost (excl. shipping/fees)", _cost_rows(analysis))
    lines += fmt.section("🔧 CPU upgrade", _upgrade_rows(analysis))
    lines += fmt.section("📊 Score breakdown", _score_rows(analysis))

    notes = []
    if analysis.get("legacy_note"):
        notes.append(f"⚠️ {fmt.esc(analysis['legacy_note'])}")
    if analysis.get("gpu"):
        notes.append(f"🤖 AI: {fmt.esc(analysis.get('ai_note'))}")
    if notes:
        lines += ["", *notes]

    # The CPU note and the RAM estimate note are already shown in their sections.
    shown = {analysis.get("cpu_note"), analysis.get("ram_upgrade_note")}
    checks = [f"• {fmt.esc(entry)}" for entry in analysis.get("missing") or [] if entry not in shown]
    checks.append(fmt.italic("Specs are inferred from listing text and the local CPU catalog; verify with the seller."))
    lines += fmt.expandable("🔎 To check", checks)

    if analysis.get("description"):
        desc = analysis["description"]
        if len(desc) >= MAX_DESCRIPTION_CHARS:
            # analyze_hardware already cut it; end on a whole word.
            desc = desc[:MAX_DESCRIPTION_CHARS - 1].rsplit(" ", 1)[0].rstrip() + " …"
        lines += fmt.expandable("📝 Seller description", [fmt.esc(desc)])
    return lines

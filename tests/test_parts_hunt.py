"""Parts Hunt title classification, using real vinted.it titles (Nov 2026)."""
import unittest
from decimal import Decimal

import parts_hunt

CAPS = parts_hunt.merge_caps(None)


def analyze(title, price):
    return parts_hunt.analyze(title, Decimal(str(price)), CAPS)


class RamTests(unittest.TestCase):
    def assertPart(self, title, price, cap_key, band="target", note=None):
        candidate, over = analyze(title, price)
        self.assertIsNotNone(candidate, title)
        self.assertFalse(over)
        self.assertEqual((candidate["cap_key"], candidate["band"]), (cap_key, band), title)
        if note:
            self.assertTrue(any(note in text for text in candidate["notes"]), candidate["notes"])
        return candidate

    def test_wanted_modules(self):
        self.assertPart("16Gb 1x16GB Samsung Sodimm DDR4 2666MHz", 50, "ram_16")
        self.assertPart("Mémoire ram 32gb (16*2) 2400 pc4 SODIMM ddr4", 80, "ram_2x16")
        self.assertPart("Kit RAM 16 Go (2x8 Go) DDR4 2666 MHz Sodimm Kingston PC portable", 55, "ram_2x8")
        self.assertPart("Ram Sodimm 8GB+8GB DDR4 2400MHz SK hynix Compatibile laptop", 65, "ram_2x8", band="discount")
        self.assertPart("2 x 8GB 2666Mhz DDR4 Sodimm RAM", 50, "ram_2x8")
        self.assertPart("Kit 16GB RAM ddr4 Notebook 2400MHz sodimm", 60, "ram_2x8", note="total of two modules")
        self.assertPart("Ram Sodimm Ddr4 Gowe 2x16gb 2666mhz", 80, "ram_2x16")
        self.assertPart("1x SODIMM ddr4 pc4 RAM Kingston 1x8GB(ram13)", 25, "ram_8")
        self.assertPart("RAM SK hynix 8 Go DDR4 2400 MHz so-dimm PC4-2400T PC Portable", 22, "ram_8")
        self.assertPart("Memoria RAM 8GB DDR4 2666MHz para portátil (SODIMM)", 32, "ram_8", band="discount")

    def test_ambiguous_layout_and_generation_become_notes(self):
        self.assertPart("Ram Ddr4 16gb Sodimm (Notebook)", 54.99, "ram_16", "discount", "2×8 GB")
        self.assertPart("Apple sodimm 16gb 2400mhz", 50, "ram_16")  # 2400 MHz implies DDR4
        self.assertPart("Ram 16Go Sk-hynix Sodimm", 50, "ram_16", note="DDR generation not stated")
        self.assertPart("Kit 32GB DDR4 sodimm", 80, "ram_2x16", note="total of two modules")
        self.assertPart("DDR4 32GB so-dimm", 80, "ram_2x16", note="2×16 GB kit or one 32 GB")

    def test_unwanted_memory_is_rejected(self):
        for title in (
            "Barrette RAM DDR2 SO-DIMM 1 Go Kingston PC2-6400 (800 MHz)",
            "Ram SQP 8GB Sodimm 1333 MHz",               # DDR3 speed, no generation
            "RAM 4 Go DDR4 SO-DIMM 2400 MHz Ramaxel – PC portable",
            "Sodimm DDR4 2x4Gb 2400mhz",
            "Kingston Fury Beast DDR4 16GB (2x8GB) 3600MHz",   # desktop kit
            "6x SK Hynix 8GB DDR4 RAM Laptop Arbeitsspeicher",
            "48GB DDR4 Laptop ram So-dimm – Samsung & SK Hynix – 6x 8GB – 2400/2666 MHz",
            "crucial 64gb ram (2x32gb) ddr4 2666mhz so-dimm",
            "Kingston FURY Beast DDR4 16GB 3200",         # desktop DIMM
            "Corsair Vengeance LPX 16GB DDR4",
            "Samsung 16GB DDR4 ECC Registered server",
            "Kit 32GB DDR5 SO-DIMM 4800",
            "Portatile Lenovo IdeaPad 3 15,6 pollici, Ryzen 3 5300u, 8 gb ram, ssd 256 gb",
            "Portatile Hp pav 15 16gb RAM ssd 1TB 15.6\"",
        ):
            self.assertEqual(analyze(title, 20), (None, False), title)

    def test_price_bands(self):
        self.assertEqual(analyze("16gb ddr4 2400 SODIMM laptopgeheugen SK Hynix", "53.50")[0]["over_pct"], 7)
        candidate, over = analyze("Ram Ddr4 Sodimm SkHynix 16gb 2666mhz", 60)
        self.assertEqual((candidate, over), (None, True))
        self.assertEqual(analyze("Some unrelated jacket", 5), (None, False))


class SsdTests(unittest.TestCase):
    def assertSsd(self, title, price, cap_key, note=None):
        candidate, over = analyze(title, price)
        self.assertIsNotNone(candidate, title)
        self.assertEqual(candidate["cap_key"], cap_key, title)
        if note:
            self.assertTrue(any(note in text for text in candidate["notes"]), candidate["notes"])
        return candidate

    def test_wanted_drives(self):
        self.assertSsd("SSD Western Digital WD Blue SN550 500GB M.2 NVMe", 35, "nvme_500")
        self.assertSsd("SSD NVMe 1 To Samsung PM9A1 M.2 2280 - Santé 96% Vérifié SMART", 110, "nvme_1tb")
        self.assertSsd("Samsung SSD PM981a de 1 to", 90, "nvme_1tb")
        self.assertSsd("Micron 1TB NVMe M.2 SSD Gen 4x4 2550 (medium length)", 105, "nvme_1tb", "Gen3 speed")
        self.assertSsd("SSD NVMe 2TB m2 2280 pcie", 180, "nvme_2tb")
        self.assertSsd("Pny ssd Sata III 1 TB", 85, "sata_1tb", "caddy")
        self.assertSsd("SSD Samsung PM851a 1TB (1024GB) 2.5\" SATA – Ottime condizioni", 80, "sata_1tb")
        self.assertSsd("SanDisk Ultra II SSD 960 GB  Top Zustand 98%", 85, "sata_1tb")
        self.assertSsd("Kingston NV3 1TB M.2 2230 NVMe PCIe 4.0 SSD", 110, "nvme_1tb", "SSD-0")
        # Console upgrade drives are standard M.2 NVMe / 2.5" SATA drives.
        self.assertSsd("Ssd Nvme 1Tb gen 4 - compatibile Ps5", 100, "nvme_1tb")

    def test_unstated_interface_becomes_a_note(self):
        self.assertSsd("SSD M.2 1TB", 99, "nvme_1tb", "not M.2 SATA")
        self.assertSsd("Kingspec 1TB SSD", 80, "sata_1tb", "interface not stated")

    def test_unwanted_drives_are_rejected(self):
        for title in (
            "WD Blue SA510 1TB M.2 2280 SATA",
            "Disque ssd interne 1 to intenso m.2 2280 sata neuf scellé",
            "SSD Delaihe 4 To – M.2 SATA Ngff – 990 PRO",
            "Crucial X6 2Tb SSD Portátil",
            "SSD Externo SanDisk Extreme 2TB blanco",
            "Samsung PM9A3 960 GB ssd U.2 NVMe Gen4, Dell enterprise",
            "Orico 1TB M.2 2242 Sata ssd",
            "2 × WD Red 2 To",
            "WD Red 2TB NAS 3.5 sata hard drive,smart good",
            "SSD Kingston NV3 2 x 1 To – Neuf",
            "2 SSD da 1Tb nvme M.2",
            "Set 2 SSD Pny da 480gb",
            "SSD crucial 500GB",                  # 500 GB only as NVMe
            "SSD 512GB Toshiba",
            "Fujitsu Esprimo Q556/2 1tb ssd 256gb m2",
            "Dell Latitude 5400 portatile 14 pollici Full HD, i5 8365U, 16 GB, SSD 500 GB",
            "HP Pavilion i7 13ª Gen | 16GB RAM | 1TB SSD",
            "GeekPi Argon ONE M.2 for Raspberry Pi + 1TB SSD",
            "Samsung SSD T7 1TB",
        ):
            self.assertEqual(analyze(title, 30), (None, False), title)


class CapsTests(unittest.TestCase):
    def test_merge_and_validation(self):
        caps = parts_hunt.merge_caps({"nvme_1tb": "95.5"})
        self.assertEqual((caps["nvme_1tb"], caps["sata_1tb"]), (Decimal("95.5"), 85))
        for bad in ({"ram_32": 10}, {"ram_8": 0}, {"ram_8": "x"}, ["ram_8"]):
            with self.assertRaises(ValueError):
                parts_hunt.merge_caps(bad)


if __name__ == "__main__":
    unittest.main()

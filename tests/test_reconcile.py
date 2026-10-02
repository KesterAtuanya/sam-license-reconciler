import unittest
from datetime import date, timedelta

from sam_reconciler.actions import build_actions
from sam_reconciler.models import PER_CORE, PER_DEVICE, PER_USER, SITE, Dataset, Device, Entitlement, Install, Product
from sam_reconciler.normalize import build_rules, normalize_installs
from sam_reconciler.reconcile import licensable_cores, reconcile

TODAY = date(2026, 10, 1)
RECENT = TODAY - timedelta(days=5)
OLD = TODAY - timedelta(days=200)


def dev(i, user="", cores=8, kind="workstation"):
    return Device(f"d{i}", f"pc-{i}", kind, cores, user)


def inst(i, device, name="Microsoft Visio Professional 2021", last_used=RECENT):
    return Install(f"i{i}", device, name, "", "", last_used)


def data(devices, installs, ents, products=()):
    return Dataset(list(devices), list(installs), list(ents), list(products), "test", TODAY)


def position(result, product):
    return next(p for p in result.positions if p.product == product)


class NormalizationTests(unittest.TestCase):
    def test_spelling_variants_become_one_product(self):
        names = ["Microsoft Visio Professional 2021 (64-bit)", "Visio Pro 2021", "Microsoft Visio Professional 2021 - en-us"]
        installs = [inst(n, "d1", name) for n, name in enumerate(names)]
        normalize_installs(installs, build_rules(None))
        self.assertEqual({i.product for i in installs}, {"Microsoft Visio Professional 2021"})

    def test_specific_edition_wins(self):
        installs = [inst(1, "d1", "AutoCAD LT 2025"), inst(2, "d1", "AutoCAD 2025")]
        normalize_installs(installs, build_rules(None))
        self.assertEqual([i.product for i in installs], ["Autodesk AutoCAD LT", "Autodesk AutoCAD"])

    def test_unmatched_titles_are_counted(self):
        installs = [inst(1, "d1", "Some Internal Tool"), inst(2, "d2", "Some Internal Tool")]
        unmatched = normalize_installs(installs, build_rules(None))
        self.assertEqual(unmatched["Some Internal Tool"], 2)

    def test_source_normalized_product_is_kept(self):
        i = Install("i1", "d1", "whatever", product="Custom Product")
        normalize_installs([i], build_rules(None))
        self.assertEqual(i.product, "Custom Product")


class PerDeviceTests(unittest.TestCase):
    def test_shortfall_and_exposure(self):
        devices = [dev(n) for n in range(5)]
        installs = [inst(n, f"d{n}") for n in range(5)]
        r = reconcile(data(devices, installs, [Entitlement("e1", "Microsoft Visio Professional 2021", 3, PER_DEVICE, 500)]),
                      build_rules(None))
        p = position(r, "Microsoft Visio Professional 2021")
        self.assertEqual((p.entitled, p.consumed, p.balance), (3, 5, -2))
        self.assertEqual(p.exposure, 1000)
        self.assertEqual(p.status, "Shortfall")

    def test_two_installs_on_one_device_count_once(self):
        installs = [inst(1, "d1", "Visio Pro 2021"), inst(2, "d1", "Microsoft Visio Professional 2021")]
        r = reconcile(data([dev(1)], installs, []), build_rules(None))
        self.assertEqual(position(r, "Microsoft Visio Professional 2021").consumed, 1)

    def test_reclaim_fixes_shortfall(self):
        devices = [dev(n) for n in range(4)]
        installs = [inst(0, "d0"), inst(1, "d1"), inst(2, "d2", last_used=OLD), inst(3, "d3", last_used=OLD)]
        r = reconcile(data(devices, installs, [Entitlement("e1", "Microsoft Visio Professional 2021", 3, PER_DEVICE, 500)]),
                      build_rules(None))
        p = position(r, "Microsoft Visio Professional 2021")
        self.assertEqual(p.reclaimable, 2)
        self.assertEqual(p.balance_after_reclaim, 1)
        self.assertEqual(p.exposure_after_reclaim, 0)
        self.assertEqual(p.status, "Fixable by reclaim")

    def test_missing_usage_is_never_reclaimed(self):
        r = reconcile(data([dev(1)], [inst(1, "d1", last_used=None)], []), build_rules(None))
        p = position(r, "Microsoft Visio Professional 2021")
        self.assertEqual((p.reclaimable, p.no_usage_data), (0, 1))

    def test_unlicensed(self):
        r = reconcile(data([dev(1)], [inst(1, "d1", "WinZip 28.0")], [], [Product("WinZip", "Corel", PER_DEVICE, 35)]),
                      build_rules(None))
        p = position(r, "WinZip")
        self.assertEqual(p.status, "Unlicensed")
        self.assertEqual(p.exposure, 35)


class PerUserTests(unittest.TestCase):
    def test_user_with_two_devices_counts_once(self):
        devices = [dev(1, "ana"), dev(2, "ana"), dev(3, "ben")]
        installs = [inst(n, f"d{n}", "Adobe Acrobat Pro DC") for n in (1, 2, 3)]
        ents = [Entitlement("e1", "Adobe Acrobat Pro", 2, PER_USER, 240)]
        p = position(reconcile(data(devices, installs, ents), build_rules(None)), "Adobe Acrobat Pro")
        self.assertEqual(p.consumed, 2)
        self.assertEqual(p.status, "Compliant")

    def test_user_is_reclaimable_only_if_unused_everywhere(self):
        devices = [dev(1, "ana"), dev(2, "ana")]
        installs = [inst(1, "d1", "Adobe Acrobat Pro DC", OLD), inst(2, "d2", "Adobe Acrobat Pro DC", RECENT)]
        ents = [Entitlement("e1", "Adobe Acrobat Pro", 1, PER_USER, 240)]
        p = position(reconcile(data(devices, installs, ents), build_rules(None)), "Adobe Acrobat Pro")
        self.assertEqual(p.reclaimable, 0)


class PerCoreTests(unittest.TestCase):
    def test_minimum_cores_per_server(self):
        product = Product("Windows Server 2022 Datacenter", "Microsoft", PER_CORE, 385, min_cores=16)
        self.assertEqual(licensable_cores(dev(1, cores=8, kind="server"), product), 16)
        self.assertEqual(licensable_cores(dev(1, cores=24, kind="server"), product), 24)

    def test_core_factor_and_two_core_packs(self):
        oracle = Product("Oracle Database Enterprise Edition", "Oracle", PER_CORE, 47500, core_factor=0.5)
        self.assertEqual(licensable_cores(dev(1, cores=16, kind="server"), oracle), 8)
        odd = Product("X", "Y", PER_CORE, 1, min_cores=0)
        self.assertEqual(licensable_cores(dev(1, cores=3, kind="server"), odd), 4)

    def test_server_shortfall_in_cores(self):
        product = Product("Microsoft SQL Server Enterprise 2022", "Microsoft", PER_CORE, 3600, min_cores=4)
        devices = [dev(1, cores=16, kind="server"), dev(2, cores=2, kind="server")]
        installs = [inst(n, f"d{n}", "Microsoft SQL Server 2022 Enterprise") for n in (1, 2)]
        ents = [Entitlement("e1", product.name, 16, PER_CORE, 3600)]
        p = position(reconcile(data(devices, installs, ents, [product]), build_rules(None)), product.name)
        self.assertEqual(p.consumed, 20)
        self.assertEqual(p.exposure, 4 * 3600)


class EntitlementTests(unittest.TestCase):
    def test_expired_rights_do_not_count(self):
        ents = [Entitlement("e1", "Microsoft Visio Professional 2021", 1, PER_DEVICE, 500, end_date=TODAY - timedelta(days=1)),
                Entitlement("e2", "Microsoft Visio Professional 2021", 1, PER_DEVICE, 500)]
        p = position(reconcile(data([dev(1)], [inst(1, "d1")], ents), build_rules(None)), "Microsoft Visio Professional 2021")
        self.assertEqual((p.entitled, p.expired_rights), (1, 1))

    def test_weighted_unit_price(self):
        ents = [Entitlement("e1", "Microsoft Visio Professional 2021", 60, PER_DEVICE, 560),
                Entitlement("e2", "Microsoft Visio Professional 2021", 40, PER_DEVICE, 600)]
        p = position(reconcile(data([], [], ents), build_rules(None)), "Microsoft Visio Professional 2021")
        self.assertEqual(p.unit_price, 576)
        self.assertEqual(p.shelfware, 100 * 576)

    def test_site_license_is_always_covered(self):
        ents = [Entitlement("e1", "Zoom Workplace", 1, SITE)]
        installs = [inst(n, f"d{n}", "Zoom") for n in range(50)]
        p = position(reconcile(data([], installs, ents), build_rules(None)), "Zoom Workplace")
        self.assertEqual((p.status, p.exposure), ("Covered", 0))

    def test_free_software_is_left_out(self):
        r = reconcile(data([dev(1)], [inst(1, "d1", "7-Zip 23.01")], [], [Product("7-Zip", "", free=True)]),
                      build_rules(None))
        self.assertEqual(r.positions, [])
        self.assertEqual(r.free_installs["7-Zip"], 1)


class ActionTests(unittest.TestCase):
    def test_renewal_inside_window_suggests_right_size(self):
        ents = [Entitlement("e1", "Adobe Acrobat Pro", 10, PER_USER, 240, end_date=TODAY + timedelta(days=30))]
        devices = [dev(n, f"u{n}") for n in range(6)]
        installs = [inst(n, f"d{n}", "Adobe Acrobat Pro DC") for n in range(6)]
        actions = build_actions(reconcile(data(devices, installs, ents), build_rules(None)))
        renew = [a for a in actions if a.kind == "renew"]
        self.assertEqual(len(renew), 1)
        self.assertIn("renew 6 instead of 10", renew[0].text)

    def test_reclaim_comes_before_buy(self):
        devices = [dev(n) for n in range(6)]
        installs = [inst(n, f"d{n}", last_used=OLD if n < 2 else RECENT) for n in range(6)]
        ents = [Entitlement("e1", "Microsoft Visio Professional 2021", 2, PER_DEVICE, 500)]
        kinds = [a.kind for a in build_actions(reconcile(data(devices, installs, ents), build_rules(None)))]
        self.assertLess(kinds.index("reclaim"), kinds.index("buy"))


class DemoTests(unittest.TestCase):
    def test_demo_is_repeatable_and_tells_its_story(self):
        from sam_reconciler.sources.demo import build_demo_dataset

        a = reconcile(build_demo_dataset(), build_rules(None))
        b = reconcile(build_demo_dataset(), build_rules(None))
        self.assertEqual(a.exposure, b.exposure)
        statuses = {p.status for p in a.positions}
        for s in ("Shortfall", "Fixable by reclaim", "Unlicensed", "Surplus", "Compliant", "Covered"):
            self.assertIn(s, statuses)
        self.assertLess(a.exposure_after_reclaim, a.exposure)


if __name__ == "__main__":
    unittest.main()

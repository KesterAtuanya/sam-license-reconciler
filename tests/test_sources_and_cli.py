import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from sam_reconciler.cli import main
from sam_reconciler.sources.csv_source import CsvError, load_folder, parse_date, parse_metric
from sam_reconciler.sources.servicenow import ServiceNowClient, ServiceNowError, load_mapping


class CsvTests(unittest.TestCase):
    def write(self, folder, name, text):
        (Path(folder) / name).write_text(text, encoding="utf-8")

    def test_reads_folder_and_tolerates_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "devices.csv", "Device_ID,Name,Cores,Assigned_User\nd1,pc-1,8,ana\n")
            self.write(tmp, "installs.csv", "device_id,display_name,last_used\nd1,Visio Pro 2021,09/15/2026\n")
            self.write(tmp, "entitlements.csv", 'product,rights,metric,unit_cost\nMicrosoft Visio Professional 2021,"1,000",Per Device,$560\n')
            data = load_folder(tmp, date(2026, 10, 1))
        self.assertEqual(data.devices[0].assigned_user, "ana")
        self.assertEqual(data.installs[0].last_used, date(2026, 9, 15))
        self.assertEqual(data.entitlements[0].rights, 1000)
        self.assertEqual(data.entitlements[0].unit_cost, 560)

    def test_missing_column_has_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "devices.csv", "name\npc-1\n")
            with self.assertRaisesRegex(CsvError, "device_id"):
                load_folder(tmp)

    def test_metric_names_from_other_tools(self):
        self.assertEqual(parse_metric("Per Named User"), "per_user")
        self.assertEqual(parse_metric("Per Core (with CAL)"), "per_core")
        self.assertEqual(parse_metric("Enterprise Agreement"), "site")
        with self.assertRaises(CsvError):
            parse_metric("per moon phase")

    def test_bad_date(self):
        with self.assertRaises(CsvError):
            parse_date("next tuesday")


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.headers = status, payload or {}, {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.headers, self.auth, self.calls = list(responses), {}, None, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


class ServiceNowTests(unittest.TestCase):
    def test_load_uses_mapping_and_divides_total_cost(self):
        s = FakeSession([
            FakeResponse(200, {"result": [{"sys_id": "i1", "installed_on": "c1", "display_name": "Visio",
                                           "norm_product.name": "Microsoft Visio Professional 2021"}]}),
            FakeResponse(200, {"result": [{"sys_id": "c1", "name": "pc-1", "sys_class_name": "cmdb_ci_computer",
                                           "cpu_core_count": "8", "assigned_to.user_name": "ana"}]}),
            FakeResponse(200, {"result": [{"sys_id": "e1", "software_model.product.name": "Microsoft Visio Professional 2021",
                                           "rights": "10", "license_metric.name": "Per Device", "cost": "5000",
                                           "start_date": "2026-01-01", "end_date": ""}]}),
        ])
        data = ServiceNowClient("dev1", "u", "p", session=s).load(load_mapping(None), date(2026, 10, 1))
        self.assertEqual(data.installs[0].product, "Microsoft Visio Professional 2021")
        self.assertEqual(data.devices[0].assigned_user, "ana")
        self.assertEqual(data.entitlements[0].unit_cost, 500)
        self.assertIn("norm_productISNOTEMPTY", s.calls[0][1]["sysparm_query"])
        self.assertIn("sys_idINc1", s.calls[1][1]["sysparm_query"])

    def test_missing_table_message(self):
        s = FakeSession([FakeResponse(400, text='{"error":{"message":"Invalid table cmdb_sam_sw_install"}}')])
        with self.assertRaisesRegex(ServiceNowError, "SAM Pro installed"):
            ServiceNowClient("dev1", "u", "p", session=s).load(load_mapping(None))

    def test_mapping_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            path.write_text(json.dumps({"installs": {"fields": {"last_used": "u_last_used"}}}))
            mapping = load_mapping(str(path))
        self.assertEqual(mapping["installs"]["fields"]["last_used"], "u_last_used")
        self.assertEqual(mapping["installs"]["table"], "cmdb_sam_sw_install")


class CliTests(unittest.TestCase):
    def test_demo_writes_reports_and_csv_round_trip_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("sys.stdout"):
                self.assertEqual(main(["--demo", "--out", f"{tmp}/a", "--export-sample", f"{tmp}/csv"]), 0)
                self.assertEqual(main(["--csv", f"{tmp}/csv", "--as-of", "2026-10-01", "--out", f"{tmp}/b"]), 0)
            a = json.loads(Path(f"{tmp}/a/license_summary.json").read_text())
            b = json.loads(Path(f"{tmp}/b/license_summary.json").read_text())
            self.assertEqual(a["exposure"], b["exposure"])
            self.assertTrue(Path(f"{tmp}/a/license_position.html").exists())
            self.assertTrue(Path(f"{tmp}/a/reclaim_candidates.csv").exists())

    def test_bad_csv_folder_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stderr"):
            self.assertEqual(main(["--csv", tmp, "--out", tmp]), 2)


if __name__ == "__main__":
    unittest.main()

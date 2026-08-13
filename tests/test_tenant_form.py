import unittest

import tenants


class TenantFormTests(unittest.TestCase):
    def test_empty_numeric_fields_are_treated_as_zero(self):
        row = {
            "ipju_jumin_no": "",
            "bojung_amt": "",
            "rent_amt": "",
            "manage_amt": "",
            "yechi_amt": "",
        }

        form = tenants._tenant_form_from_row(row)

        self.assertEqual(form["bojung_amt"], "0")
        self.assertEqual(form["rent_amt"], "0")
        self.assertEqual(form["manage_amt"], "0")
        self.assertEqual(form["yechi_amt"], "0")

    def test_tenant_search_query_uses_name_first(self):
        form = {
            "ipju_nm": "홍길동",
            "ipju_tel1": "010-1234-5678",
            "ipju_tel2": "",
            "ipju_tel3": "",
            "bunji1": "0508",
            "bunji2": "0088",
            "hosu": "201",
        }
        self.assertEqual(tenants._tenant_search_query(form), "홍길동")


if __name__ == "__main__":
    unittest.main()

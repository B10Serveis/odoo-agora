import ast
import json
from unittest.mock import Mock, patch

import requests
from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAgoraPosConfig(TransactionCase):
    def setUp(self):
        super().setUp()
        self.config = self.env["pos.config"].create({"name": "Agora test POS"})

    def test_agora_configuration_is_stored_per_pos(self):
        config = self.config
        self.assertFalse(config.is_agora)

        config.write({
            "is_agora": True,
            "agora_api_url": "https://agora.example.test/api",
            "agora_api_token": "test-secret-token",
        })
        config.invalidate_recordset()

        self.assertTrue(config.is_agora)
        self.assertEqual(config.agora_api_url, "https://agora.example.test/api")
        self.assertEqual(config.agora_api_token, "test-secret-token")

    def test_api_credentials_are_admin_only(self):
        config = self.config
        admin_fields = config.fields_get(["agora_api_url", "agora_api_token"])
        self.assertEqual(
            set(admin_fields), {"agora_api_url", "agora_api_token"}
        )

        pos_manager = self.env["res.users"].create({
            "name": "Agora POS manager",
            "login": "agora_pos_manager",
            "groups_id": [
                (6, 0, [
                    self.env.ref("base.group_user").id,
                    self.env.ref("point_of_sale.group_pos_manager").id,
                ]),
            ],
        })
        restricted_config = config.with_user(pos_manager)
        restricted_fields = restricted_config.fields_get(
            ["agora_api_url", "agora_api_token"]
        )
        self.assertFalse(restricted_fields)
        with self.assertRaises(AccessError):
            restricted_config.read(["agora_api_token"])

    def test_credentials_are_only_shown_for_enabled_agora_pos(self):
        view = self.env.ref("agora_integration.pos_config_view_form_agora")
        arch = etree.fromstring(view.arch_db.encode())
        agora_group = arch.xpath("//group[@string='Integració Àgora']")[0]
        self.assertEqual(agora_group.get("groups"), "base.group_system")
        self.assertEqual(
            ast.literal_eval(agora_group.get("attrs"))["invisible"],
            [("is_agora", "=", False)],
        )
        self.assertEqual(
            set(agora_group.xpath("./field/@name")),
            {"agora_api_url", "agora_api_token"},
        )
        self.assertEqual(
            arch.xpath("//field[@name='agora_api_token']/@password"), ["True"]
        )
        self.assertEqual(
            arch.xpath("//button[@name='action_test_agora_connection']/@type"),
            ["object"],
        )
        for field_name in ("agora_api_url", "agora_api_token"):
            self.assertEqual(
                self.config._fields[field_name].groups, "base.group_system"
            )

    def test_odoo_pos_options_are_hidden_for_agora(self):
        view = self.env["pos.config"].get_view(
            view_id=self.env.ref("point_of_sale.pos_config_view_form").id,
            view_type="form",
        )
        arch = etree.fromstring(view["arch"].encode())
        normal_settings = arch.xpath(
            "//div[@id='other_devices']/ancestor::div["
            "contains(concat(' ', normalize-space(@class), ' '), "
            "' o_settings_container ')][1]"
        )[0]
        normal_modifiers = json.loads(normal_settings.get("modifiers", "{}"))
        self.assertEqual(
            normal_modifiers["invisible"], [["is_agora", "=", True]]
        )

        create_view = self.env["pos.config"].with_context(
            pos_config_create_mode=True
        ).get_view(
            view_id=self.env.ref("point_of_sale.pos_config_view_form").id,
            view_type="form",
        )
        create_arch = etree.fromstring(create_view["arch"].encode())
        create_settings = create_arch.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_settings_container ')][.//field[@name='module_pos_restaurant']]"
        )[0]
        create_modifiers = json.loads(create_settings.get("modifiers", "{}"))
        self.assertEqual(
            create_modifiers["invisible"], [["is_agora", "=", True]]
        )

        self.assertTrue(arch.xpath("//field[@name='is_agora']"))

    def test_general_pos_settings_follow_selected_pos(self):
        self.config.is_agora = True
        settings = self.env["res.config.settings"].new({
            "pos_config_id": self.config.id,
        })
        self.assertTrue(settings.pos_config_is_agora)

        view = self.env["res.config.settings"].get_view(
            view_id=self.env.ref(
                "point_of_sale.res_config_settings_view_form"
            ).id,
            view_type="form",
        )
        arch = etree.fromstring(view["arch"].encode())
        settings_content = arch.xpath(
            "//div[@id='pos_interface_section']/parent::div"
        )[0]
        modifiers = json.loads(settings_content.get("modifiers", "{}"))
        self.assertEqual(
            modifiers["invisible"],
            ["|", ["pos_config_id", "=", False], ["pos_config_is_agora", "=", True]],
        )
        self.assertTrue(arch.xpath("//field[@name='pos_config_is_agora']"))
        self.assertTrue(arch.xpath("//field[@name='pos_config_id']"))

    def test_agora_connection_check_success(self):
        self.config.write({
            "is_agora": True,
            "agora_api_url": "https://agora.example.test:8984/",
            "agora_api_token": "secret-api-token",
        })
        with patch(
            "odoo.addons.agora_integration.models.pos_config.requests.get",
            return_value=Mock(status_code=200),
        ) as http_get:
            action = self.config.action_test_agora_connection()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")
        http_get.assert_called_once_with(
            "https://agora.example.test:8984/api/export-master/",
            params={"filter": "WorkplacesSummary"},
            headers={
                "Api-Token": "secret-api-token",
                "Accept": "application/json",
            },
            timeout=10,
            allow_redirects=False,
        )

    def test_agora_connection_check_reports_authentication_failure(self):
        self.config.write({
            "is_agora": True,
            "agora_api_url": "https://agora.example.test:8984",
            "agora_api_token": "invalid-api-token",
        })
        with patch(
            "odoo.addons.agora_integration.models.pos_config.requests.get",
            return_value=Mock(status_code=401),
        ):
            action = self.config.action_test_agora_connection()

        self.assertEqual(action["params"]["type"], "danger")
        self.assertIn("401", action["params"]["message"])

    def test_agora_connection_check_handles_timeout(self):
        self.config.write({
            "is_agora": True,
            "agora_api_url": "https://agora.example.test:8984",
            "agora_api_token": "secret-api-token",
        })
        with patch(
            "odoo.addons.agora_integration.models.pos_config.requests.get",
            side_effect=requests.exceptions.Timeout,
        ):
            action = self.config.action_test_agora_connection()

        self.assertEqual(action["params"]["type"], "warning")

    def test_agora_connection_check_requires_admin_and_configuration(self):
        pos_manager = self.env["res.users"].create({
            "name": "Agora POS manager for connection test",
            "login": "agora_pos_manager_connection",
            "groups_id": [
                (6, 0, [
                    self.env.ref("base.group_user").id,
                    self.env.ref("point_of_sale.group_pos_manager").id,
                ]),
            ],
        })
        with self.assertRaises(AccessError):
            self.config.with_user(pos_manager).action_test_agora_connection()
        with self.assertRaises(UserError):
            self.config.action_test_agora_connection()

        self.config.is_agora = True
        with self.assertRaises(UserError):
            self.config.action_test_agora_connection()

    def test_agora_connection_cannot_be_reused_by_another_pos(self):
        self.config.write({
            "is_agora": True,
            "agora_api_url": "https://agora.example.test/api/",
            "agora_api_token": "same-token",
        })
        with self.assertRaises(ValidationError):
            self.env["pos.config"].create({
                "name": "Duplicate Agora test POS",
                "is_agora": True,
                "agora_api_url": "https://AGORA.example.test/api",
                "agora_api_token": "same-token",
            })


@tagged("post_install", "-at_install")
class TestAgoraTaxes(TransactionCase):
    def setUp(self):
        super().setUp()
        self.config = self.env["pos.config"].create({
            "name": "Agora tax import POS",
            "is_agora": True,
            "agora_api_url": "https://agora.example.test:8984",
            "agora_api_token": "tax-import-token",
        })
        self.Tax = self.env["pos.agora.tax"]

    def _mock_response(self, vats, status_code=200):
        response = Mock(status_code=status_code)
        response.json.return_value = {"Vats": vats}
        return response

    def test_import_creates_then_updates_taxes_for_pos(self):
        first_response = self._mock_response([{
            "Id": 4,
            "Name": "General",
            "VatRate": 0.21,
            "SurchargeRate": 0.052,
        }])
        with patch(
            "odoo.addons.agora_integration.models.pos_agora_tax.requests.get",
            return_value=first_response,
        ) as http_get:
            result = self.Tax.import_from_pos_config(self.config)

        self.assertEqual(result, {"created": 1, "updated": 0})
        imported_tax = self.Tax.search([
            ("pos_config_id", "=", self.config.id), ("agora_id", "=", 4),
        ])
        self.assertEqual(len(imported_tax), 1)
        self.assertEqual(imported_tax.name, "General")
        self.assertAlmostEqual(imported_tax.vat_rate, 0.21)
        self.assertAlmostEqual(imported_tax.surcharge_rate, 0.052)
        self.assertTrue(imported_tax.enabled)
        self.assertEqual(imported_tax.mapping_status, "unmapped")
        http_get.assert_called_once_with(
            "https://agora.example.test:8984/api/export-master/",
            params={"filter": "Vats"},
            headers={
                "Api-Token": "tax-import-token",
                "Accept": "application/json",
            },
            timeout=20,
            allow_redirects=False,
        )

        updated_response = self._mock_response([{
            "Id": 4,
            "Name": "General actualitzat",
            "VatRate": 0.21,
            "SurchargeRate": 0.052,
            "Enabled": False,
        }])
        with patch(
            "odoo.addons.agora_integration.models.pos_agora_tax.requests.get",
            return_value=updated_response,
        ):
            result = self.Tax.import_from_pos_config(self.config)

        self.assertEqual(result, {"created": 0, "updated": 1})
        self.assertEqual(self.Tax.search_count([
            ("pos_config_id", "=", self.config.id), ("agora_id", "=", 4),
        ]), 1)
        imported_tax.invalidate_recordset()
        self.assertEqual(imported_tax.name, "General actualitzat")
        self.assertFalse(imported_tax.enabled)

    def test_import_rejects_invalid_payload_without_partial_changes(self):
        response = self._mock_response([
            {
                "Id": 1,
                "Name": "Exempt",
                "VatRate": 0.0,
                "SurchargeRate": 0.0,
            },
            {
                "Id": 11,
                "Name": "Invalid",
                "VatRate": 0.21,
                "SurchargeRate": 0.0,
            },
        ])
        with patch(
            "odoo.addons.agora_integration.models.pos_agora_tax.requests.get",
            return_value=response,
        ):
            with self.assertRaises(UserError):
                self.Tax.import_from_pos_config(self.config)

        self.assertFalse(self.Tax.search([
            ("pos_config_id", "=", self.config.id),
        ]))

    def test_imported_tax_identity_includes_pos_config(self):
        other_config = self.env["pos.config"].create({
            "name": "Other Agora tax import POS",
            "is_agora": True,
            "agora_api_url": "https://other-agora.example.test",
            "agora_api_token": "other-token",
        })
        common_values = {
            "agora_id": 1,
            "name": "Exempt",
            "vat_rate": 0.0,
            "surcharge_rate": 0.0,
        }
        first = self.Tax.create({**common_values, "pos_config_id": self.config.id})
        second = self.Tax.create({**common_values, "pos_config_id": other_config.id})
        self.assertNotEqual(first, second)
        self.assertEqual(first.agora_id, second.agora_id)

    def test_mapping_uses_company_sales_taxes_and_reports_completion(self):
        imported_tax = self.Tax.create({
            "pos_config_id": self.config.id,
            "agora_id": 2,
            "name": "Reduced",
            "vat_rate": 0.10,
            "surcharge_rate": 0.014,
        })
        tax_group = self.env.ref("account.tax_group_taxes")
        odoo_vat = self.env["account.tax"].create({
            "name": "Agora test VAT",
            "company_id": self.config.company_id.id,
            "country_id": self.env.ref("base.es").id,
            "tax_group_id": tax_group.id,
            "type_tax_use": "sale",
            "amount_type": "percent",
            "amount": 10,
        })
        odoo_surcharge = self.env["account.tax"].create({
            "name": "Agora test surcharge",
            "company_id": self.config.company_id.id,
            "country_id": self.env.ref("base.es").id,
            "tax_group_id": tax_group.id,
            "type_tax_use": "sale",
            "amount_type": "percent",
            "amount": 1.0,
        })

        imported_tax.odoo_tax_id = odoo_vat
        self.assertEqual(imported_tax.mapping_status, "partial")
        imported_tax.odoo_surcharge_tax_id = odoo_surcharge
        self.assertEqual(imported_tax.mapping_status, "rate_mismatch")
        odoo_surcharge.amount = 1.4
        self.assertEqual(imported_tax.mapping_status, "mapped")

    def test_import_is_limited_to_pos_managers(self):
        internal_user = self.env["res.users"].create({
            "name": "Agora tax import internal user",
            "login": "agora_tax_import_internal_user",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(AccessError):
            self.Tax.with_user(internal_user).import_from_pos_config(self.config)

    def test_pos_manager_can_import_without_reading_api_credentials(self):
        pos_manager = self.env["res.users"].create({
            "name": "Agora tax import POS manager",
            "login": "agora_tax_import_pos_manager",
            "groups_id": [
                (6, 0, [
                    self.env.ref("base.group_user").id,
                    self.env.ref("point_of_sale.group_pos_manager").id,
                ]),
            ],
        })
        restricted_config = self.config.with_user(pos_manager)
        self.assertFalse(restricted_config.fields_get(["agora_api_token"]))
        with patch(
            "odoo.addons.agora_integration.models.pos_agora_tax.requests.get",
            return_value=self._mock_response([]),
        ):
            result = self.Tax.with_user(pos_manager).import_from_pos_config(
                restricted_config
            )
        self.assertEqual(result, {"created": 0, "updated": 0})

    def test_agora_taxes_menu_is_installed_under_point_of_sale(self):
        menu = self.env.ref("agora_integration.menu_pos_agora_taxes")
        action = self.env.ref("agora_integration.action_pos_agora_tax_import_wizard")
        self.assertEqual(menu.parent_id, self.env.ref("agora_integration.menu_pos_agora_root"))
        self.assertEqual(
            self.env.ref("agora_integration.menu_pos_agora_root").parent_id,
            self.env.ref("point_of_sale.menu_point_root"),
        )
        self.assertEqual(action.res_model, "pos.agora.tax.import.wizard")

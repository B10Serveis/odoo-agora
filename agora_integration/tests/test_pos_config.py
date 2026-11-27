import ast
import json
from unittest.mock import Mock, patch

import requests
from lxml import etree

from odoo.exceptions import AccessError, UserError
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

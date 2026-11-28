import math
from urllib.parse import urljoin, urlsplit

import requests

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero


class PosAgoraTax(models.Model):
    _name = "pos.agora.tax"
    _description = "Impost importat d'Àgora"
    _order = "pos_config_id, agora_id"
    _check_company_auto = True
    _rec_name = "name"

    _AGORA_DATA_FIELDS = {
        "agora_id",
        "name",
        "vat_rate",
        "surcharge_rate",
        "enabled",
        "last_imported_at",
        "pos_config_id",
    }

    pos_config_id = fields.Many2one(
        "pos.config",
        string="Punt de venda Àgora",
        required=True,
        index=True,
        ondelete="cascade",
        domain=[("is_agora", "=", True)],
    )
    company_id = fields.Many2one(
        related="pos_config_id.company_id",
        string="Empresa",
        store=True,
        readonly=True,
        index=True,
    )
    agora_id = fields.Integer(
        string="ID d'Àgora",
        required=True,
        readonly=True,
        group_operator=False,
    )
    name = fields.Char(string="Nom a Àgora", required=True, readonly=True)
    vat_rate = fields.Float(
        string="IVA d'Àgora",
        digits=(16, 6),
        required=True,
        readonly=True,
        group_operator=False,
    )
    surcharge_rate = fields.Float(
        string="Recàrrec d'equivalència d'Àgora",
        digits=(16, 6),
        required=True,
        readonly=True,
        group_operator=False,
    )
    enabled = fields.Boolean(string="Actiu a Àgora", readonly=True)
    last_imported_at = fields.Datetime(
        string="Última importació",
        readonly=True,
        copy=False,
    )
    odoo_tax_id = fields.Many2one(
        "account.tax",
        string="Impost d'IVA d'Odoo",
        check_company=True,
        domain="[('company_id', '=', company_id), ('type_tax_use', '=', 'sale')]",
    )
    odoo_surcharge_tax_id = fields.Many2one(
        "account.tax",
        string="Impost de recàrrec d'Odoo",
        check_company=True,
        domain="[('company_id', '=', company_id), ('type_tax_use', '=', 'sale')]",
    )
    mapping_status = fields.Selection(
        [
            ("unmapped", "Pendent de mapar"),
            ("partial", "Mapping parcial"),
            ("mapped", "Mapeig correcte"),
            ("rate_mismatch", "Tipus impositiu diferent"),
        ],
        string="Estat del mapping",
        compute="_compute_mapping_status",
        store=True,
    )

    _sql_constraints = [
        (
            "pos_agora_tax_id_unique",
            "unique(pos_config_id, agora_id)",
            "Aquest impost d'Àgora ja existeix per a aquest punt de venda.",
        ),
        (
            "pos_agora_tax_id_range",
            "check(agora_id >= 1 and agora_id <= 10)",
            "L'ID d'un impost d'Àgora ha d'estar entre 1 i 10.",
        ),
    ]

    @api.depends(
        "vat_rate",
        "surcharge_rate",
        "odoo_tax_id",
        "odoo_tax_id.amount",
        "odoo_tax_id.amount_type",
        "odoo_surcharge_tax_id",
        "odoo_surcharge_tax_id.amount",
        "odoo_surcharge_tax_id.amount_type",
    )
    def _compute_mapping_status(self):
        for tax in self:
            has_surcharge = not float_is_zero(tax.surcharge_rate, precision_digits=6)
            if not tax.odoo_tax_id and not tax.odoo_surcharge_tax_id:
                tax.mapping_status = "unmapped"
            elif not tax.odoo_tax_id or (has_surcharge and not tax.odoo_surcharge_tax_id):
                tax.mapping_status = "partial"
            elif (
                not self._odoo_tax_matches_rate(tax.odoo_tax_id, tax.vat_rate)
                or (
                    has_surcharge
                    and not self._odoo_tax_matches_rate(
                        tax.odoo_surcharge_tax_id, tax.surcharge_rate
                    )
                )
                or (not has_surcharge and tax.odoo_surcharge_tax_id)
            ):
                tax.mapping_status = "rate_mismatch"
            else:
                tax.mapping_status = "mapped"

    @api.model
    def _odoo_tax_matches_rate(self, odoo_tax, agora_rate):
        return bool(
            odoo_tax
            and odoo_tax.amount_type == "percent"
            and float_compare(
                odoo_tax.amount,
                agora_rate * 100,
                precision_digits=4,
            ) == 0
        )

    @api.constrains("odoo_tax_id", "odoo_surcharge_tax_id", "company_id")
    def _check_odoo_tax_mappings(self):
        for record in self:
            for tax in (record.odoo_tax_id, record.odoo_surcharge_tax_id):
                if tax and tax.type_tax_use != "sale":
                    raise ValidationError(_(
                        "Només es poden mapar impostos d'Odoo d'ús en vendes."
                    ))

    def write(self, values):
        if (
            self._AGORA_DATA_FIELDS.intersection(values)
            and not self.env.context.get("_agora_tax_import")
        ):
            raise AccessError(_(
                "Les dades importades d'Àgora només es poden modificar actualitzant la importació."
            ))
        return super().write(values)

    @api.model
    def import_from_pos_config(self, pos_config):
        """Fetch and upsert the VAT catalog for one configured Agora POS."""
        pos_config.ensure_one()
        if not (
            self.env.user.has_group("base.group_system")
            or self.env.user.has_group("point_of_sale.group_pos_manager")
        ):
            raise AccessError(_(
                "Només els responsables del punt de venda poden importar impostos d'Àgora."
            ))
        pos_config.check_access_rights("read")
        pos_config.check_access_rule("read")
        if not pos_config.sudo().is_agora:
            raise UserError(_(
                "El punt de venda seleccionat no està configurat com a Àgora."
            ))

        # POS managers may run the import without being able to read the stored
        # credentials; the request is made server-side and never returns them.
        pos_config = pos_config.sudo()
        base_url = (pos_config.agora_api_url or "").strip()
        token = (pos_config.agora_api_token or "").strip()
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise UserError(_(
                "Indica una URL vàlida amb el protocol HTTP o HTTPS a la configuració del POS."
            ))
        if not token:
            raise UserError(_(
                "Indica el token de l'API d'Àgora a la configuració del POS."
            ))

        endpoint = urljoin(base_url.rstrip("/") + "/", "/api/export-master/")
        try:
            response = requests.get(
                endpoint,
                params={"filter": "Vats"},
                headers={"Api-Token": token, "Accept": "application/json"},
                timeout=20,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as error:
            raise UserError(_(
                "Temps d'espera esgotat en consultar els impostos d'Àgora."
            )) from error
        except requests.exceptions.RequestException as error:
            raise UserError(_(
                "No s'ha pogut consultar l'API d'Àgora per importar els impostos."
            )) from error

        if response.status_code != 200:
            if response.status_code in (401, 403):
                message = _(
                    "Àgora ha rebutjat el token de l'API (HTTP %(status)s).",
                    status=response.status_code,
                )
            else:
                message = _(
                    "El servidor d'Àgora ha respost amb l'estat HTTP %(status)s.",
                    status=response.status_code,
                )
            raise UserError(message)

        try:
            payload = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError) as error:
            raise UserError(_(
                "La resposta d'Àgora no conté un document JSON vàlid."
            )) from error

        vat_rows = payload.get("Vats") if isinstance(payload, dict) else None
        if not isinstance(vat_rows, list):
            raise UserError(_(
                "La resposta d'Àgora no conté la llista d'impostos 'Vats'."
            ))

        normalized_rows = []
        seen_ids = set()
        for row in vat_rows:
            normalized_rows.append(self._normalize_agora_vat(row, seen_ids))

        Tax = self.sudo().with_context(_agora_tax_import=True)
        existing_taxes = Tax.search([("pos_config_id", "=", pos_config.id)])
        taxes_by_agora_id = {tax.agora_id: tax for tax in existing_taxes}
        imported_at = fields.Datetime.now()
        created_count = 0
        updated_count = 0
        for values in normalized_rows:
            values.update({
                "pos_config_id": pos_config.id,
                "last_imported_at": imported_at,
            })
            existing = taxes_by_agora_id.get(values["agora_id"])
            if existing:
                existing.write(values)
                updated_count += 1
            else:
                Tax.create(values)
                created_count += 1
        return {"created": created_count, "updated": updated_count}

    @api.model
    def _normalize_agora_vat(self, row, seen_ids):
        if not isinstance(row, dict):
            raise UserError(_("Àgora ha retornat un impost amb un format incorrecte."))

        agora_id = row.get("Id")
        if isinstance(agora_id, bool) or not isinstance(agora_id, int):
            raise UserError(_("L'ID d'un impost retornat per Àgora no és vàlid."))
        if not 1 <= agora_id <= 10:
            raise UserError(_(
                "L'ID %(id)s retornat per Àgora és fora del rang permès (1–10).",
                id=agora_id,
            ))
        if agora_id in seen_ids:
            raise UserError(_(
                "Àgora ha retornat més d'una vegada l'impost amb ID %(id)s.",
                id=agora_id,
            ))
        seen_ids.add(agora_id)

        name = row.get("Name")
        if not isinstance(name, str) or not name.strip():
            raise UserError(_(
                "El nom de l'impost amb ID %(id)s retornat per Àgora no és vàlid.",
                id=agora_id,
            ))

        vat_rate = self._normalize_rate(row.get("VatRate"), "VatRate", agora_id)
        surcharge_rate = self._normalize_rate(
            row.get("SurchargeRate"), "SurchargeRate", agora_id
        )
        enabled = row.get("Enabled", True)
        if not isinstance(enabled, bool):
            raise UserError(_(
                "L'estat de l'impost amb ID %(id)s retornat per Àgora no és vàlid.",
                id=agora_id,
            ))
        return {
            "agora_id": agora_id,
            "name": name.strip(),
            "vat_rate": vat_rate,
            "surcharge_rate": surcharge_rate,
            "enabled": enabled,
        }

    @api.model
    def _normalize_rate(self, value, field_name, agora_id):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UserError(_(
                "El camp %(field)s de l'impost %(id)s retornat per Àgora no és vàlid.",
                field=field_name,
                id=agora_id,
            ))
        rate = float(value)
        if not math.isfinite(rate) or rate < 0:
            raise UserError(_(
                "El camp %(field)s de l'impost %(id)s retornat per Àgora no és vàlid.",
                field=field_name,
                id=agora_id,
            ))
        return rate

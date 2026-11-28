from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PosAgoraTaxImportWizard(models.TransientModel):
    _name = "pos.agora.tax.import.wizard"
    _description = "Importar impostos d'Àgora"

    pos_config_id = fields.Many2one(
        "pos.config",
        string="Punt de venda Àgora",
        required=True,
        domain=[("is_agora", "=", True)],
    )

    @api.constrains("pos_config_id")
    def _check_agora_pos(self):
        for wizard in self:
            if wizard.pos_config_id and not wizard.pos_config_id.is_agora:
                raise UserError(_(
                    "Només es poden consultar impostos de punts de venda configurats com a Àgora."
                ))

    def action_view_taxes(self):
        self.ensure_one()
        return self._get_taxes_action()

    def action_import_taxes(self):
        self.ensure_one()
        self.env["pos.agora.tax"].import_from_pos_config(self.pos_config_id)
        return self._get_taxes_action()

    def _get_taxes_action(self):
        action = self.env.ref("agora_integration.action_pos_agora_tax").read()[0]
        action["domain"] = [("pos_config_id", "=", self.pos_config_id.id)]
        action["context"] = {
            **self.env.context,
            "default_pos_config_id": self.pos_config_id.id,
            "search_default_group_by_pos_config": 1,
        }
        return action

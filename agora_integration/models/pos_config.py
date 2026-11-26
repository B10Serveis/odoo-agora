from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    is_agora = fields.Boolean(string="Punt de venda Àgora")
    agora_api_url = fields.Char(
        string="URL de l'API d'Àgora",
        groups="base.group_system",
    )
    agora_api_token = fields.Char(
        string="Token de l'API d'Àgora",
        groups="base.group_system",
    )

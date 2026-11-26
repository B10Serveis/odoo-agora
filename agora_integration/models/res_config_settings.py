from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_config_is_agora = fields.Boolean(related="pos_config_id.is_agora")

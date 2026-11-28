import hashlib
import json
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


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
    agora_connection_key = fields.Char(
        compute="_compute_agora_connection_key",
        store=True,
        index=True,
        copy=False,
        groups="base.group_system",
    )

    _sql_constraints = [
        (
            "agora_connection_key_unique",
            "unique(agora_connection_key)",
            "Aquesta connexió d'Àgora ja està assignada a un altre punt de venda.",
        ),
    ]

    @api.depends("agora_api_url", "agora_api_token")
    def _compute_agora_connection_key(self):
        for config in self:
            base_url = (config.agora_api_url or "").strip()
            token = (config.agora_api_token or "").strip()
            if not base_url or not token:
                config.agora_connection_key = False
                continue

            parsed_url = urlsplit(base_url)
            if parsed_url.scheme and parsed_url.netloc:
                normalized_url = urlunsplit((
                    parsed_url.scheme.lower(),
                    parsed_url.netloc.lower(),
                    parsed_url.path.rstrip("/"),
                    parsed_url.query,
                    "",
                ))
            else:
                normalized_url = base_url.rstrip("/")
            connection_data = json.dumps(
                [normalized_url, token], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            config.agora_connection_key = hashlib.sha256(connection_data).hexdigest()

    @api.constrains("agora_api_url", "agora_api_token")
    def _check_unique_agora_connection(self):
        for config in self:
            if not config.agora_connection_key:
                continue
            # A normal ORM search flushes the pending computed key first, so
            # the SQL unique constraint would fire before this friendly error.
            self.env.cr.execute(
                "SELECT 1 FROM pos_config "
                "WHERE agora_connection_key = %s AND id != %s LIMIT 1",
                (config.agora_connection_key, config.id),
            )
            if self.env.cr.fetchone():
                raise ValidationError(_(
                    "La mateixa URL i el mateix token d'Àgora no es poden assignar "
                    "a més d'un punt de venda."
                ))

    def action_test_agora_connection(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "Només els administradors poden provar la connexió d'Àgora."
            ))
        if not self.is_agora:
            raise UserError(_("Activa la integració Àgora abans de provar la connexió."))

        base_url = (self.agora_api_url or "").strip()
        token = (self.agora_api_token or "").strip()
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise UserError(_("Indica una URL vàlida amb el protocol HTTP o HTTPS."))
        if not token:
            raise UserError(_(
                "Indica el token de l'API d'Àgora abans de provar la connexió."
            ))

        # The guide has no health endpoint; this light master-data request checks
        # both connectivity and the API token without exporting sales data.
        endpoint = urljoin(base_url.rstrip("/") + "/", "/api/export-master/")
        try:
            response = requests.get(
                endpoint,
                params={"filter": "WorkplacesSummary"},
                headers={"Api-Token": token, "Accept": "application/json"},
                timeout=10,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout:
            return self._agora_connection_notification(
                _("Temps d'espera esgotat"),
                _("El servidor d'Àgora no ha respost dins del temps límit."),
                "warning",
            )
        except requests.exceptions.ConnectionError:
            return self._agora_connection_notification(
                _("No s'ha pogut connectar"),
                _("No s'ha pogut establir connexió amb el servidor d'Àgora."),
                "danger",
            )
        except requests.exceptions.RequestException:
            return self._agora_connection_notification(
                _("Error de connexió"),
                _("S'ha produït un error en consultar l'API d'Àgora."),
                "danger",
            )

        if response.status_code == 200:
            return self._agora_connection_notification(
                _("Connexió correcta"),
                _("El servidor d'Àgora ha respost correctament i ha acceptat el token."),
                "success",
            )
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
        return self._agora_connection_notification(
            _("No s'ha pogut validar la connexió"), message, "danger"
        )

    @api.model
    def _agora_connection_notification(self, title, message, notification_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": False,
            },
        }

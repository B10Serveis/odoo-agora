{
    "name": "Integració amb Àgora",
    "summary": "Configuració de la integració Àgora per punt de venda",
    "version": "16.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Batista10",
    "license": "AGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "security/ir.model.access.csv",
        "security/pos_agora_tax_security.xml",
        "views/pos_config_views.xml",
        "views/res_config_settings_views.xml",
        "views/pos_agora_tax_views.xml",
        "views/pos_agora_tax_import_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}

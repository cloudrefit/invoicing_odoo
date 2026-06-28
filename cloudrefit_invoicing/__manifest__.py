{
    'name': 'CloudRefit ZATCA Gateway',
    'version': '19.0.0.59',
    'category': 'Accounting',
    'summary': 'بوابة كلاود ريفيت ZATCA',
    'description': """
CloudRefit ZATCA Gateway for Odoo 19.
Connects Odoo to the CloudRefit ZATCA Gateway for Phase 2 compliance.
""",
    'author': 'CloudRefit',
    'website': 'https://www.cloudrefit.com',
    'license': 'LGPL-3',
    'depends': ['account', 'base'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'data/ir_actions.xml',
        'data/ir_actions_todo.xml',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/account_move_views.xml',
        'views/report_invoice.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
    'assets': {
        'web.assets_backend': [
            'cloudrefit_invoicing/static/src/js/zatca_timer.js',
            'cloudrefit_invoicing/static/src/xml/zatca_timer.xml',
        ],
    },
}

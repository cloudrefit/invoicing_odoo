{
    'name': 'CloudRefit ZATCA Gateway',
    'version': '19.0.0.31',
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
        'views/cloudrefit_zatca_sandbox_test_views.xml',
        'views/account_move_views.xml',
        'views/report_invoice.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
}

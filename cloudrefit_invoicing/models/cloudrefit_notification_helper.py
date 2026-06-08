from odoo import models


class CloudrefitNotificationHelper(models.AbstractModel):
    _name = 'cloudrefit.notification.helper'
    _description = 'CloudRefit Notification Helper Mixin'

    def _cr_notify(self, alert_type, message, sticky=None, next_action=None):
        """Return a client-side notification action dict."""
        if sticky is None:
            sticky = alert_type in ('danger', 'warning')
        action = {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': alert_type,
                'message': message,
                'sticky': sticky,
            },
        }
        if next_action:
            action['params']['next'] = next_action
        return action

/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onWillStart, onWillDestroy } from "@odoo/owl";

export class ZatcaTimerWidget extends Component {
    setup() {
        this.state = useState({ remaining: 120, hidden: false, text: "" });
        this.timer = null;
        
        onWillStart(() => {
            this.updateTimer();
            this.timer = setInterval(() => this.updateTimer(), 1000);
        });

        onWillDestroy(() => {
            if (this.timer) clearInterval(this.timer);
        });
    }

    updateTimer() {
        const pushed_at = this.props.record.data[this.props.name];
        if (!pushed_at) {
            this.state.hidden = true;
            return;
        }
        
        // pushed_at is a luxon DateTime in Odoo
        const diff = luxon.DateTime.now().diff(pushed_at, 'seconds').seconds;
        if (diff >= 120) {
            this.state.hidden = true;
        } else {
            const rem = Math.floor(120 - diff);
            const m = Math.floor(rem / 60);
            const s = rem % 60;
            this.state.text = `${m}:${s.toString().padStart(2, '0')}`;
            this.state.hidden = false;
        }
    }
}

ZatcaTimerWidget.template = "cloudrefit_invoicing.ZatcaTimer";
ZatcaTimerWidget.props = {
    ...standardFieldProps,
};

registry.category("fields").add("zatca_timer", ZatcaTimerWidget);

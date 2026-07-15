/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onWillDestroy } from "@odoo/owl";

export class ZatcaTimerWidget extends Component {
    setup() {
        this.action = useService("action");
        this.rpc = useService("rpc");
        this.state = useState({ remaining: 120, hidden: false, text: "Processing..." });
        this.displayTimer = null;
        this.pollTimer = null;
        this.synced = false;
        
        onWillStart(() => {
            this.updateDisplayTimer();
            this.displayTimer = setInterval(() => this.updateDisplayTimer(), 1000);
            
            // Poll for status every 5 seconds
            this.pollTimer = setInterval(() => this.pollStatus(), 5000);
        });

        onWillDestroy(() => {
            if (this.displayTimer) clearInterval(this.displayTimer);
            if (this.pollTimer) clearInterval(this.pollTimer);
        });
    }

    async pollStatus() {
        if (this.synced) return;
        
        const invoiceId = this.props.record.resId;
        if (!invoiceId) return;
        
        try {
            const response = await this.rpc(`/cloudrefit/api/invoice_status/${invoiceId}`);
            if (response && response.status) {
                const status = response.status;
                // If status is no longer pending or processing, we reload the view
                if (status !== 'pending' && status !== 'processing') {
                    this.synced = true;
                    if (this.displayTimer) clearInterval(this.displayTimer);
                    if (this.pollTimer) clearInterval(this.pollTimer);
                    this.state.hidden = true;
                    this.action.doAction({ type: "ir.actions.client", tag: "reload" });
                }
            }
        } catch (error) {
            console.warn("Failed to poll CloudRefit invoice status", error);
        }
    }

    updateDisplayTimer() {
        if (this.synced) return;
        
        const pushed_at = this.props.record.data[this.props.name];
        if (!pushed_at) {
            this.state.hidden = true;
            return;
        }
        
        // pushed_at is a luxon DateTime in Odoo
        const diff = luxon.DateTime.now().diff(pushed_at, 'seconds').seconds;
        if (diff >= 120) {
            this.state.hidden = true;
            if (this.displayTimer) clearInterval(this.displayTimer);
            if (this.pollTimer) clearInterval(this.pollTimer);
            // We just hide it and let the user refresh manually if it takes too long
        } else {
            const rem = Math.floor(120 - diff);
            const m = Math.floor(rem / 60);
            const s = rem % 60;
            this.state.text = `Processing... ${m}:${s.toString().padStart(2, '0')}`;
            this.state.hidden = false;
        }
    }
}

ZatcaTimerWidget.template = "cloudrefit_invoicing.ZatcaTimer";
ZatcaTimerWidget.props = {
    ...standardFieldProps,
};

export const zatcaTimerField = {
    component: ZatcaTimerWidget,
    supportedTypes: ["datetime"],
};

registry.category("fields").add("zatca_timer", zatcaTimerField);

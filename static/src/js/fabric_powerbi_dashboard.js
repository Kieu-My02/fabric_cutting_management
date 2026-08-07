/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";

/**
 * Client action nhúng dashboard Power BI (link "Publish to Web") vào
 * menu Tổng quan (Insights) của module Cấp phát Vải.
 *
 * Cách lấy link:
 *  Power BI Service -> mở report -> File -> Embed report ->
 *  Publish to web (public) -> Create embed code -> copy URL trong
 *  thuộc tính src của iframe (dạng https://app.powerbi.com/view?r=...)
 */
export class FabricPowerBiDashboard extends Component {
    static template = "fabric_cutting_management.PowerBiDashboard";

    setup() {
        // TODO: thay bằng link "Publish to Web" thật của bạn
        this.embedUrl = "https://app.powerbi.com/reportEmbed?reportId=3ea3fab9-2ac5-41d9-83fd-0d962aba1383&autoAuth=true&ctid=14d5de2b-d212-4175-92d5-156ea5b7c037";
    }
}

registry.category("actions").add("fabric_powerbi_dashboard", FabricPowerBiDashboard);

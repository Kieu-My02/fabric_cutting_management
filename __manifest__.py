# -*- coding: utf-8 -*-
{
    'name': 'Fabric Cutting Room Management (Cấp phát vải Phòng Cắt)',
    'version': '19.0.1.13.0',
    'category': 'Inventory/Inventory',
    'summary': 'QC gate xuất kho, đổi trả/lãnh bù vải, báo xuất thiếu, quét mã cây vải cho Far Eastern Apparel',

    'description': """
Fabric Cutting Room Management
===============================

Module tùy chỉnh cho đồ án "Phân tích và triển khai hệ thống Odoo trong quản lý
cấp phát vải tại Phòng Cắt - Công ty Far Eastern Apparel" (Nhóm 6).

Chức năng chính:
-----------------
* Mở rộng stock.lot để quản lý cây vải: Roll ID, khổ vải, ánh màu, số lot nhuộm,
  trạng thái QC (PASS/FAIL).

* Chặn (gate) thao tác xuất kho cho Phòng Cắt nếu cây vải chưa PASS QC.

* Trừ tồn theo QC Pass: cây vải nhập về nằm ở vị trí phụ "Khu chờ QC", tách
  biệt khỏi kho khả dụng; khi QC PASS hệ thống tự chuyển vào kho chính.

* Module Đổi Trả / Lãnh Bù:
  - Lãnh vải bù
  - Báo xuất thiếu
  - Trả vải dư

* Wizard Báo xuất thiếu.

* Wizard quét Barcode.

* Chỉ số đánh giá nhà cung cấp.

* Định mức GSM / khổ vải.

* Gợi ý cây vải cùng Lot nhuộm.

* Truy vết cây vải.

* Báo cáo chênh lệch định mức.

* Dự báo thiếu vải.

* Tính nhu cầu vải từ Đơn bán hàng.

* Gộp nhiều đơn thành 1 PO tổng.

* Dashboard sản xuất, thanh toán, định mức.

* Thanh toán mua - bán.

* Outbound cấp phát vải.

* Tổng hợp lượng vải cho đơn may.

* Dashboard Insights & Supplier Scorecard.

* Cọc mua vải theo NCC: % cọc mặc định riêng từng NCC, số tiền cọc chốt
  tại thời điểm xác nhận PO, liên kết hoá đơn cọc.

* Kiểm tra PO đã PASS QC toàn bộ (mọi cây vải nhận về đều PASS) hay chưa.
""",

    'author': 'Nhóm 6 - UTH',
    'website': '',
    'license': 'LGPL-3',

    'depends': [
        'stock',
        'purchase',
        'mrp',
        'sale',
        'sale_stock',
        'account',
        'barcodes',
        'mail',
        'payment',      # Chỉ giữ nếu module thực sự dùng payment.provider
    ],

    'data': [

        # ==========================
        # SECURITY
        # ==========================
        'security/fabric_security.xml',
        'security/ir.model.access.csv',

        # ==========================
        # DATA
        # ==========================
        'data/sequence_data.xml',
        'data/fabric_qc_location_data.xml',
        'data/payment_provider_data.xml',

        # ==========================
        # VIEWS
        # ==========================
        'views/stock_lot_views.xml',
        'views/stock_picking_views.xml',
        'views/purchase_order_views.xml',
        'views/fabric_return_views.xml',
        'views/fabric_shortage_views.xml',
        'views/barcode_scan_views.xml',
        'views/product_template_views.xml',
        'views/sale_order_views.xml',
        'views/mrp_production_views.xml',

        # Reports
        'views/fabric_norm_variance_report_views.xml',
        'views/fabric_production_insight_report_views.xml',
        'views/fabric_shortage_forecast_report_views.xml',
        'views/fabric_supplier_scorecard_report_views.xml',

        # Wizards
        'views/fabric_consolidated_po_wizard_views.xml',

        # Payment
        'views/payment_provider_views.xml',

        # Dashboard
        'views/fabric_powerbi_dashboard_actions.xml',

        # Menu
        'views/menu_views.xml',
    ],

    'assets': {
        'web.assets_backend': [
            'fabric_cutting_management/static/src/js/fabric_powerbi_dashboard.js',
            'fabric_cutting_management/static/src/xml/fabric_powerbi_dashboard.xml',
        ],
    },

    'installable': True,
    'application': True,
}
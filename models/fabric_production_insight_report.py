# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from .fabric_norm_variance_report import DEFAULT_NORM_VARIANCE_THRESHOLD


class FabricProductionInsightReport(models.Model):
    """FR-15 (Nhóm 5-6): Bảng tổng hợp Sản xuất - Thanh toán - Định mức.

    Đây KHÔNG phải báo cáo chi tiết theo từng dòng nguyên liệu như FR-12
    (fabric.norm.variance.report), mà là 1 dòng / 1 Lệnh Cắt (mrp.production)
    - gộp lại đúng 3 mảng nghiệp vụ mà đồ án yêu cầu bổ sung:

    1) Ghi nhận SX hoàn thành: trạng thái chuẩn (state = done) của Lệnh Cắt,
       cùng mốc giai đoạn thủ công Cắt/May/Hoàn thành (production_stage, xem
       mrp_production.py).
    2) Thanh toán: payment_state/amount_residual tổng hợp từ hoá đơn của Đơn
       hàng bán tương ứng (xem sale_order.py) - suy luận qua dây chuyền MTO
       chuẩn (mrp.production.sale_order_id).
    3) Cảnh báo định mức: gộp lại (SUM/BOOL_OR) toàn bộ dòng nguyên liệu vải
       của Lệnh Cắt, dùng lại đúng công thức ngưỡng cảnh báo của FR-12 -
       không tính lại theo cách khác để tránh 2 báo cáo lệch số liệu nhau.

    Cũng là model report dạng SQL VIEW (_auto=False), cùng tinh thần với
    fabric_norm_variance_report.py / fabric_shortage_forecast_report.py:
    chỉ tổng hợp lại dữ liệu đã có sẵn, không yêu cầu nhập liệu thêm. Chỉ
    hiển thị các Lệnh Cắt CÓ dùng vải (JOIN với fabric_usage) - đúng phạm vi
    của module này.

    Nhóm 10 - Thống kê tùy nhu cầu: bổ sung thêm cột thời gian SX
    (production_lead_time_days, xem bên dưới) và cột NCC cấp vải chính
    (main_fabric_supplier_id) để có thể lọc/nhóm nhanh Insights theo Nhà
    cung cấp - đối chiếu chéo sang Thẻ điểm đánh giá Nhà cung cấp (FR-16,
    fabric.supplier.scorecard.report) mà không cần đổi mô hình dữ liệu gốc."""

    _name = 'fabric.production.insight.report'
    _description = 'FR-15: Tổng hợp Sản xuất - Thanh toán - Định mức (Insights)'
    _auto = False
    _order = 'date_finished desc, production_id desc'

    production_id = fields.Many2one('mrp.production', string='Lệnh Cắt (Cut Ticket)', readonly=True)
    company_id = fields.Many2one('res.company', string='Công ty', readonly=True)

    state = fields.Selection(
        [
            ('draft', 'Nháp'), ('confirmed', 'Đã xác nhận'), ('progress', 'Đang thực hiện'),
            ('to_close', 'Chờ đóng'), ('done', 'Đã hoàn thành'), ('cancel', 'Đã hủy'),
        ],
        string='Trạng thái Lệnh Cắt', readonly=True,
    )
    production_stage = fields.Selection(
        [
            ('cutting', 'Đang cắt'),
            ('sewing', 'Đang may'),
            ('completed', 'Hoàn thành'),
        ],
        string='Giai đoạn SX', readonly=True,
    )
    date_planned_start = fields.Datetime(string='Ngày bắt đầu KH', readonly=True)
    date_finished = fields.Datetime(string='Ngày hoàn thành SX', readonly=True)

    sale_order_id = fields.Many2one('sale.order', string='Đơn hàng bán', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Khách hàng', readonly=True)
    payment_state = fields.Selection(
        [
            ('not_invoiced', 'Chưa xuất hoá đơn'),
            ('not_paid', 'Chưa thanh toán'),
            ('in_payment', 'Đang xử lý thanh toán'),
            ('partial', 'Thanh toán một phần'),
            ('paid', 'Đã thanh toán'),
            ('reversed', 'Đã bị đảo (Reversed)'),
        ],
        string='Tình trạng thanh toán', readonly=True,
    )
    amount_total = fields.Monetary(
        string='Giá trị đơn hàng', readonly=True, currency_field='currency_id',
        help='Giá trị của CẢ Đơn hàng bán (không phải riêng Lệnh Cắt này). CẢNH BÁO: '
             '1 Đơn hàng bán có thể sinh ra NHIỀU Lệnh Cắt (production_ids trên '
             'sale.order) - nếu SUM cột này trên Pivot theo nhóm khác sale_order_id '
             '(vd: theo Giai đoạn SX, theo NCC), số liệu sẽ bị NHÂN BẢN theo số Lệnh '
             'Cắt của cùng 1 đơn hàng. Chỉ SUM an toàn khi group_by = Đơn hàng bán, '
             'hoặc dùng measure Count để biết số dòng trước khi diễn giải số tiền.')
    amount_residual = fields.Monetary(
        string='Còn phải thu', readonly=True, currency_field='currency_id',
        help='Cùng cảnh báo double-count như amount_total ở trên - giá trị này thuộc '
             'về Đơn hàng bán, bị lặp lại trên từng Lệnh Cắt cùng đơn hàng.')
    related_production_count = fields.Integer(
        string='Số Lệnh Cắt cùng Đơn hàng', readonly=True,
        help='Số lượng Lệnh Cắt khác cùng thuộc 1 Đơn hàng bán - dùng để phát hiện '
             'nguy cơ double-count Giá trị đơn hàng/Còn phải thu khi > 1.')
    currency_id = fields.Many2one('res.currency', string='Tiền tệ', readonly=True)

    fabric_planned_qty = fields.Float(
        string='Định mức lý thuyết (BoM)', readonly=True, digits='Product Unit of Measure')
    fabric_actual_qty = fields.Float(
        string='Thực tế đã xuất', readonly=True, digits='Product Unit of Measure')
    fabric_variance_qty = fields.Float(
        string='Chênh lệch', readonly=True, digits='Product Unit of Measure')
    fabric_variance_percent = fields.Float(
        string='% Chênh lệch', readonly=True, digits=(12, 2), group_operator='avg',
        help='Đây là tỉ lệ % lệch của TỪNG Lệnh Cắt. Khi xem theo nhóm/pivot, Odoo sẽ '
             'lấy trung bình cộng (avg) thay vì cộng dồn (sum) - nếu để mặc định '
             '(sum), một nhóm nhiều Lệnh Cắt cùng thiếu 100% vải sẽ cộng dồn thành '
             '-200%, -300%... rất dễ gây hiểu lầm khi trình bày trước hội đồng.')
    fabric_variance_alert = fields.Boolean(
        string='Có cảnh báo vượt ngưỡng', readonly=True,
        help='Bật khi CÓ ÍT NHẤT 1 dòng nguyên liệu vải của Lệnh Cắt vượt ngưỡng cảnh '
             'báo định mức - cùng công thức với FR-12, xem chi tiết từng dòng tại đó.')

    # Nhóm 10 - Thống kê tùy nhu cầu: 2 cột bổ sung cho dashboard Insights,
    # không tính lại gì mới - chỉ đọc thêm date_planned_start/date_finished
    # đã có sẵn ở trên, và NCC của cây vải xuất nhiều nhất cho Lệnh Cắt.
    production_lead_time_days = fields.Float(
        string='Số ngày SX thực tế', readonly=True, digits=(12, 1), group_operator='avg',
        help='= Ngày hoàn thành SX - Ngày bắt đầu KH (chỉ có giá trị khi Lệnh Cắt đã '
             'hoàn thành) - dùng để so sánh tốc độ SX giữa các Lệnh Cắt/khách hàng. '
             'group_operator=avg để tránh cộng dồn số ngày khi xem theo nhóm/pivot '
             '(cùng lý do với fabric_variance_percent ở trên).')
    main_fabric_supplier_id = fields.Many2one(
        'res.partner', string='NCC cấp vải chính', readonly=True,
        help='Nhà cung cấp của cây vải (Roll) đã xuất NHIỀU NHẤT cho Lệnh Cắt này - '
             'cho phép lọc/nhóm Insights theo NCC, đối chiếu chéo sang Thẻ điểm đánh '
             'giá Nhà cung cấp.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %(table)s AS (
                WITH fabric_usage AS (
                    SELECT
                        sm.raw_material_production_id AS production_id,
                        SUM(sm.product_uom_qty) AS planned_qty,
                        SUM(COALESCE(act.actual_qty, 0.0)) AS actual_qty,
                        BOOL_OR(
                            sm.product_uom_qty != 0 AND
                            ABS((COALESCE(act.actual_qty, 0.0) - sm.product_uom_qty)
                                / sm.product_uom_qty * 100.0)
                            > COALESCE(NULLIF(pt.fabric_norm_variance_threshold, 0.0), %(default_threshold)s)
                        ) AS variance_alert
                    FROM stock_move sm
                    JOIN product_product pp ON pp.id = sm.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    LEFT JOIN LATERAL (
                        SELECT SUM(sml.quantity) AS actual_qty
                        FROM stock_move_line sml
                        WHERE sml.move_id = sm.id AND sml.picked = true
                    ) act ON true
                    WHERE sm.raw_material_production_id IS NOT NULL
                      AND pt.is_fabric IS TRUE
                      AND sm.state != 'cancel'
                    GROUP BY sm.raw_material_production_id
                ),
                invoice_agg AS (
                    -- FR-15: sale.order KHÔNG có sẵn cột payment_state/amount_residual
                    -- (2 field này thuộc account.move - hoá đơn), nên phải tự gộp từ các
                    -- hoá đơn bán (out_invoice/out_refund) đã Post, liên kết qua
                    -- sale_order_line -> sale_order_line_invoice_rel -> account_move_line.
                    SELECT
                        inv.sale_order_id AS sale_order_id,
                        SUM(inv.amount_total) AS amount_total,
                        SUM(inv.amount_residual) AS amount_residual,
                        COUNT(*) AS invoice_count,
                        SUM(CASE WHEN inv.payment_state = 'paid' THEN 1 ELSE 0 END) AS paid_count,
                        BOOL_OR(inv.payment_state = 'partial') AS has_partial,
                        BOOL_OR(inv.payment_state = 'in_payment') AS has_in_payment,
                        BOOL_OR(inv.payment_state = 'reversed') AS has_reversed
                    FROM (
                        SELECT DISTINCT
                            sol.order_id AS sale_order_id,
                            am.id AS move_id,
                            am.amount_total AS amount_total,
                            am.amount_residual AS amount_residual,
                            am.payment_state AS payment_state
                        FROM sale_order_line sol
                        JOIN sale_order_line_invoice_rel rel ON rel.order_line_id = sol.id
                        JOIN account_move_line aml ON aml.id = rel.invoice_line_id
                        JOIN account_move am ON am.id = aml.move_id
                        WHERE am.move_type IN ('out_invoice', 'out_refund')
                          AND am.state = 'posted'
                    ) inv
                    GROUP BY inv.sale_order_id
                ),
                supplier_usage AS (
                    -- Nhóm 10: tổng số lượng đã xuất theo từng NCC (qua Lot cây vải)
                    -- cho mỗi Lệnh Cắt, để chọn ra NCC cấp vải NHIỀU NHẤT bên dưới.
                    SELECT
                        sm.raw_material_production_id AS production_id,
                        sl.fabric_supplier_id AS fabric_supplier_id,
                        SUM(sml.quantity) AS supplier_qty
                    FROM stock_move_line sml
                    JOIN stock_move sm ON sm.id = sml.move_id
                    JOIN stock_lot sl ON sl.id = sml.lot_id
                    WHERE sm.raw_material_production_id IS NOT NULL
                      AND sml.picked = true
                      AND sl.fabric_supplier_id IS NOT NULL
                    GROUP BY sm.raw_material_production_id, sl.fabric_supplier_id
                ),
                main_supplier AS (
                    SELECT DISTINCT ON (production_id)
                        production_id, fabric_supplier_id
                    FROM supplier_usage
                    ORDER BY production_id, supplier_qty DESC
                ),
                sale_order_production_count AS (
                    -- Nhóm 10 (bổ sung fix): đếm số Lệnh Cắt/1 Đơn hàng bán, để cảnh
                    -- báo double-count amount_total/amount_residual khi SUM trên Pivot
                    -- theo chiều khác sale_order_id (xem help text 2 field trên).
                    SELECT sale_order_id, COUNT(*) AS production_count
                    FROM mrp_production
                    WHERE sale_order_id IS NOT NULL AND state != 'cancel'
                    GROUP BY sale_order_id
                )
                SELECT
                    mo.id AS id,
                    mo.id AS production_id,
                    mo.company_id AS company_id,
                    mo.state AS state,
                    mo.production_stage AS production_stage,
                    mo.date_start AS date_planned_start,
                    mo.date_finished AS date_finished,
                    mo.sale_order_id AS sale_order_id,
                    so.partner_id AS partner_id,
                    CASE
                        WHEN ia.invoice_count IS NULL OR ia.invoice_count = 0 THEN 'not_invoiced'
                        WHEN ia.paid_count = ia.invoice_count THEN 'paid'
                        WHEN ia.has_reversed AND ia.paid_count = 0
                             AND NOT ia.has_partial AND NOT ia.has_in_payment THEN 'reversed'
                        WHEN ia.has_partial OR (ia.amount_residual > 0 AND ia.amount_residual < ia.amount_total)
                             THEN 'partial'
                        WHEN ia.has_in_payment THEN 'in_payment'
                        ELSE 'not_paid'
                    END AS payment_state,
                    COALESCE(so.amount_total, 0.0) AS amount_total,
                    COALESCE(ia.amount_residual, 0.0) AS amount_residual,
                    so.currency_id AS currency_id,
                    fu.planned_qty AS fabric_planned_qty,
                    fu.actual_qty AS fabric_actual_qty,
                    (fu.actual_qty - fu.planned_qty) AS fabric_variance_qty,
                    CASE WHEN fu.planned_qty != 0 THEN
                        (fu.actual_qty - fu.planned_qty) / fu.planned_qty * 100.0
                    ELSE 0.0 END AS fabric_variance_percent,
                    fu.variance_alert AS fabric_variance_alert,
                    CASE WHEN mo.date_finished IS NOT NULL AND mo.date_start IS NOT NULL THEN
                        EXTRACT(EPOCH FROM (mo.date_finished - mo.date_start)) / 86400.0
                    ELSE NULL END AS production_lead_time_days,
                    ms.fabric_supplier_id AS main_fabric_supplier_id,
                    COALESCE(spc.production_count, 0) AS related_production_count
                FROM mrp_production mo
                JOIN fabric_usage fu ON fu.production_id = mo.id
                LEFT JOIN sale_order so ON so.id = mo.sale_order_id
                LEFT JOIN invoice_agg ia ON ia.sale_order_id = mo.sale_order_id
                LEFT JOIN main_supplier ms ON ms.production_id = mo.id
                LEFT JOIN sale_order_production_count spc ON spc.sale_order_id = mo.sale_order_id
                WHERE mo.state != 'cancel'
            )
        """ % {
            'table': self._table,
            'default_threshold': DEFAULT_NORM_VARIANCE_THRESHOLD,
        })

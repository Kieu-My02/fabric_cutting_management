# -*- coding: utf-8 -*-
from odoo import fields, models, tools

# FR-12: ngưỡng cảnh báo mặc định khi product.template chưa khai báo
# fabric_norm_variance_threshold riêng cho mã vải đó.
DEFAULT_NORM_VARIANCE_THRESHOLD = 5.0  # %


class FabricNormVarianceReport(models.Model):
    """FR-12: Báo cáo chênh lệch định mức — so sánh định mức LÝ THUYẾT (nhu
    cầu nguyên liệu vải theo BoM, field product_uom_qty trên move nguyên liệu
    của Lệnh Cắt) với số lượng THỰC TẾ đã xuất dùng (tổng qty các move line
    đã "picked").

    Đây là model report dạng SQL VIEW (_auto=False) — không tạo bảng dữ liệu
    riêng, không trùng lặp dữ liệu nghiệp vụ, chỉ tổng hợp lại dữ liệu đã có
    sẵn trên stock.move / stock.move.line của Lệnh Cắt (mrp.production),
    đúng tinh thần chỉ mở rộng, không tạo lại những gì Odoo đã có (xem thêm
    ghi chú tương tự tại mrp_production.py FR-08)."""

    _name = 'fabric.norm.variance.report'
    _description = 'FR-12: Báo cáo chênh lệch định mức Cắt (Lý thuyết vs Thực tế)'
    _auto = False
    _order = 'move_date desc'

    production_id = fields.Many2one('mrp.production', string='Lệnh Cắt (Cut Ticket)', readonly=True)
    product_id = fields.Many2one('product.product', string='Loại vải', readonly=True)
    product_tmpl_id = fields.Many2one('product.template', string='Mã hàng (vải)', readonly=True)
    lot_id = fields.Many2one('stock.lot', string='Cây vải (Roll) tham chiếu', readonly=True,
                              help='Cây vải đầu tiên đã xuất cho dòng nguyên liệu này — dùng để '
                                   'đối chiếu nhanh sang FR-11 (Truy vết cây vải). Nếu một dòng '
                                   'nguyên liệu được trải từ nhiều cây vải, xem chi tiết đầy đủ '
                                   'trong Truy vết cây vải trên từng Roll liên quan.')
    uom_id = fields.Many2one('uom.uom', string='Đơn vị tính', readonly=True)
    company_id = fields.Many2one('res.company', string='Công ty', readonly=True)
    state = fields.Selection(
        [
            ('draft', 'Nháp'), ('confirmed', 'Đã xác nhận'), ('progress', 'Đang thực hiện'),
            ('to_close', 'Chờ đóng'), ('done', 'Đã hoàn thành'), ('cancel', 'Đã hủy'),
        ],
        string='Trạng thái Lệnh Cắt', readonly=True,
    )
    # Nhóm 5-6: mốc giai đoạn SX của chính Lệnh Cắt (xem mrp_production.py),
    # cho phép lọc/nhóm chênh lệch định mức theo giai đoạn Cắt/May/Hoàn
    # thành - vd. chỉ xem cảnh báo của các Lệnh Cắt ĐÃ bàn giao sang May,
    # nơi sai lệch định mức khó điều chỉnh lại hơn so với khi còn đang cắt.
    production_stage = fields.Selection(
        [
            ('cutting', 'Đang cắt'),
            ('sewing', 'Đang may'),
            ('completed', 'Hoàn thành'),
        ],
        string='Giai đoạn SX', readonly=True,
    )
    move_date = fields.Datetime(string='Ngày', readonly=True)
    planned_qty = fields.Float(string='Định mức lý thuyết (BoM)', readonly=True, digits='Product Unit of Measure')
    actual_qty = fields.Float(string='Thực tế đã xuất', readonly=True, digits='Product Unit of Measure')
    variance_qty = fields.Float(string='Chênh lệch', readonly=True, digits='Product Unit of Measure')
    variance_percent = fields.Float(string='% Chênh lệch', readonly=True, digits=(12, 2))
    variance_alert = fields.Boolean(string='Vượt ngưỡng cảnh báo', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    sm.id AS id,
                    sm.raw_material_production_id AS production_id,
                    sm.product_id AS product_id,
                    pt.id AS product_tmpl_id,
                    lot.lot_id AS lot_id,
                    sm.product_uom AS uom_id,
                    sm.company_id AS company_id,
                    mo.state AS state,
                    mo.production_stage AS production_stage,
                    COALESCE(mo.date_finished, mo.date_start, sm.date) AS move_date,
                    sm.product_uom_qty AS planned_qty,
                    COALESCE(act.actual_qty, 0.0) AS actual_qty,
                    (COALESCE(act.actual_qty, 0.0) - sm.product_uom_qty) AS variance_qty,
                    CASE WHEN sm.product_uom_qty != 0 THEN
                        (COALESCE(act.actual_qty, 0.0) - sm.product_uom_qty)
                        / sm.product_uom_qty * 100.0
                    ELSE 0.0 END AS variance_percent,
                    CASE WHEN sm.product_uom_qty != 0 AND
                        ABS((COALESCE(act.actual_qty, 0.0) - sm.product_uom_qty)
                            / sm.product_uom_qty * 100.0)
                        > COALESCE(NULLIF(pt.fabric_norm_variance_threshold, 0.0), %s)
                    THEN true ELSE false END AS variance_alert
                FROM stock_move sm
                JOIN mrp_production mo ON mo.id = sm.raw_material_production_id
                JOIN product_product pp ON pp.id = sm.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN LATERAL (
                    SELECT SUM(sml.quantity) AS actual_qty
                    FROM stock_move_line sml
                    WHERE sml.move_id = sm.id AND sml.picked = true
                ) act ON true
                LEFT JOIN LATERAL (
                    SELECT sml2.lot_id AS lot_id
                    FROM stock_move_line sml2
                    WHERE sml2.move_id = sm.id AND sml2.lot_id IS NOT NULL
                    ORDER BY sml2.id ASC
                    LIMIT 1
                ) lot ON true
                WHERE sm.raw_material_production_id IS NOT NULL
                  AND pt.is_fabric IS TRUE
                  AND sm.state = 'done'
                  AND mo.state != 'cancel'
            )
        """ % (self._table, DEFAULT_NORM_VARIANCE_THRESHOLD))
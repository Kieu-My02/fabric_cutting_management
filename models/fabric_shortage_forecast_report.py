# -*- coding: utf-8 -*-
from odoo import fields, models, tools

# FR-13: giá trị mặc định khi product.template chưa khai báo riêng
# fabric_lead_time_days / fabric_safety_stock_days cho mã vải đó.
DEFAULT_FABRIC_LEAD_TIME_DAYS = 15.0    # số ngày từ lúc đặt NCC đến lúc nhận hàng
DEFAULT_FABRIC_SAFETY_STOCK_DAYS = 7.0  # số ngày tồn an toàn mong muốn luôn có sẵn


class FabricShortageForecastReport(models.Model):
    """FR-13: Dự báo thiếu vải từ lịch sử nhập/xuất — tổng hợp toàn bộ dịch
    chuyển xuất kho (nội bộ -> Lệnh Cắt/khách hàng) đã "done" theo mã vải +
    màu, tính tốc độ tiêu thụ bình quân/ngày, từ đó suy ra tồn an toàn
    (safety stock) và điểm đặt hàng lại (reorder point) theo thời gian giao
    hàng (lead time) của NCC, rồi so với tồn khả dụng hiện tại (chỉ tính cây
    vải đã qua QC PASS) để gợi ý "cần nhập thêm X vào ngày Y".

    Cũng là model report dạng SQL VIEW (_auto=False), cùng tinh thần với
    fabric_norm_variance_report.py: không tạo bảng dữ liệu riêng, chỉ tổng
    hợp lại lịch sử đã có sẵn trên stock.move/stock.move.line/stock.quant,
    không yêu cầu công nhân nhập liệu thêm bất kỳ dữ liệu nào."""

    _name = 'fabric.shortage.forecast.report'
    _description = 'FR-13: Dự báo thiếu vải từ lịch sử nhập/xuất'
    _auto = False
    _order = 'shortage_alert desc, days_until_reorder asc'

    product_id = fields.Many2one('product.product', string='Loại vải', readonly=True)
    product_tmpl_id = fields.Many2one('product.template', string='Mã hàng (vải)', readonly=True)
    fabric_color_code = fields.Char(string='Mã màu / Ánh màu', readonly=True)
    uom_id = fields.Many2one('uom.uom', string='Đơn vị tính', readonly=True)
    company_id = fields.Many2one('res.company', string='Công ty', readonly=True)

    first_move_date = fields.Datetime(string='Lần xuất đầu tiên', readonly=True)
    last_move_date = fields.Datetime(string='Lần xuất gần nhất', readonly=True)
    days_history = fields.Float(string='Số ngày lịch sử dùng để tính', readonly=True)
    total_out_qty = fields.Float(string='Tổng đã xuất dùng', readonly=True,
                                  digits='Product Unit of Measure')
    avg_daily_consumption = fields.Float(
        string='Tốc độ tiêu thụ TB/ngày', readonly=True, digits='Product Unit of Measure')
    onhand_qty = fields.Float(
        string='Tồn khả dụng hiện tại', readonly=True, digits='Product Unit of Measure',
        help='Chỉ tính cây vải đã QC PASS (hoặc không quản lý theo Lot) — không tính '
             'hàng đang nằm ở Khu chờ QC hoặc đã bị đánh FAIL.')
    lead_time_days = fields.Float(
        string='Thời gian giao hàng NCC (ngày)', readonly=True,
        help='Lấy từ mã hàng (product.template); nếu chưa khai báo dùng mặc định %s ngày.'
             % DEFAULT_FABRIC_LEAD_TIME_DAYS)
    safety_stock_days = fields.Float(
        string='Số ngày tồn an toàn', readonly=True,
        help='Lấy từ mã hàng (product.template); nếu chưa khai báo dùng mặc định %s ngày.'
             % DEFAULT_FABRIC_SAFETY_STOCK_DAYS)
    safety_stock_qty = fields.Float(
        string='Tồn an toàn (SL)', readonly=True, digits='Product Unit of Measure',
        help='= Tốc độ tiêu thụ TB/ngày x Số ngày tồn an toàn.')
    reorder_point_qty = fields.Float(
        string='Điểm đặt hàng lại (Reorder Point)', readonly=True,
        digits='Product Unit of Measure',
        help='= Tốc độ tiêu thụ TB/ngày x (Lead time NCC + Số ngày tồn an toàn).')
    qty_to_order = fields.Float(
        string='Gợi ý SL cần nhập thêm', readonly=True, digits='Product Unit of Measure',
        help='= MAX(Điểm đặt hàng lại - Tồn khả dụng hiện tại, 0).')
    days_until_reorder = fields.Float(
        string='Số ngày đến hạn đặt hàng', readonly=True,
        help='Số ngày ước tính trước khi tồn khả dụng giảm xuống bằng Điểm đặt hàng lại. '
             'Giá trị âm/0 nghĩa là đã trễ hạn, cần đặt hàng ngay.')
    suggested_reorder_date = fields.Date(
        string='Gợi ý ngày cần đặt hàng (Y)', readonly=True,
        help='= Hôm nay + Số ngày đến hạn đặt hàng (không nhỏ hơn hôm nay).')
    shortage_alert = fields.Boolean(
        string='Cảnh báo nguy cơ thiếu vải', readonly=True,
        help='Bật khi Tồn khả dụng hiện tại đã <= Điểm đặt hàng lại.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        query = """
            CREATE OR REPLACE VIEW %(table)s AS (
                WITH consumption AS (
                    SELECT
                        sm.company_id AS company_id,
                        sm.product_id AS product_id,
                        lot.fabric_color_code AS fabric_color_code,
                        MAX(sm.product_uom) AS uom_id,
                        MIN(sm.date) AS first_move_date,
                        MAX(sm.date) AS last_move_date,
                        SUM(sml.quantity) AS total_out_qty
                    FROM stock_move_line sml
                    JOIN stock_move sm ON sm.id = sml.move_id
                    JOIN stock_location src ON src.id = sml.location_id
                    JOIN stock_location dest ON dest.id = sml.location_dest_id
                    LEFT JOIN stock_lot lot ON lot.id = sml.lot_id
                    WHERE sm.state = 'done'
                      AND src.usage = 'internal'
                      AND dest.usage IN ('production', 'customer')
                    GROUP BY sm.company_id, sm.product_id, lot.fabric_color_code
                ),
                onhand AS (
                    SELECT
                        sq.company_id AS company_id,
                        sq.product_id AS product_id,
                        lot.fabric_color_code AS fabric_color_code,
                        SUM(sq.quantity) AS onhand_qty
                    FROM stock_quant sq
                    JOIN stock_location loc ON loc.id = sq.location_id
                    LEFT JOIN stock_lot lot ON lot.id = sq.lot_id
                    WHERE loc.usage = 'internal'
                      AND (lot.id IS NULL OR lot.qc_state = 'pass')
                    GROUP BY sq.company_id, sq.product_id, lot.fabric_color_code
                ),
                base AS (
                    SELECT
                        c.company_id AS company_id,
                        c.product_id AS product_id,
                        pt.id AS product_tmpl_id,
                        c.uom_id AS uom_id,
                        c.fabric_color_code AS fabric_color_code,
                        c.first_move_date AS first_move_date,
                        c.last_move_date AS last_move_date,
                        GREATEST(EXTRACT(DAY FROM (c.last_move_date - c.first_move_date))::numeric, 1)
                            AS days_history,
                        c.total_out_qty AS total_out_qty,
                        COALESCE(o.onhand_qty, 0.0) AS onhand_qty,
                        COALESCE(NULLIF(pt.fabric_lead_time_days, 0), %(default_lead_time)s)
                            AS lead_time_days,
                        COALESCE(NULLIF(pt.fabric_safety_stock_days, 0), %(default_safety_days)s)
                            AS safety_stock_days
                    FROM consumption c
                    JOIN product_product pp ON pp.id = c.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    LEFT JOIN onhand o ON o.product_id = c.product_id
                        AND o.company_id = c.company_id
                        AND (o.fabric_color_code = c.fabric_color_code
                             OR (o.fabric_color_code IS NULL AND c.fabric_color_code IS NULL))
                    WHERE pt.is_fabric IS TRUE
                ),
                calc AS (
                    SELECT
                        b.*,
                        (b.total_out_qty / b.days_history) AS avg_daily_consumption
                    FROM base b
                )
                SELECT
                    ROW_NUMBER() OVER (ORDER BY calc.product_id, calc.fabric_color_code) AS id,
                    calc.product_id AS product_id,
                    calc.product_tmpl_id AS product_tmpl_id,
                    calc.fabric_color_code AS fabric_color_code,
                    calc.uom_id AS uom_id,
                    calc.company_id AS company_id,
                    calc.first_move_date AS first_move_date,
                    calc.last_move_date AS last_move_date,
                    calc.days_history AS days_history,
                    calc.total_out_qty AS total_out_qty,
                    calc.avg_daily_consumption AS avg_daily_consumption,
                    calc.onhand_qty AS onhand_qty,
                    calc.lead_time_days AS lead_time_days,
                    calc.safety_stock_days AS safety_stock_days,
                    (calc.avg_daily_consumption * calc.safety_stock_days) AS safety_stock_qty,
                    (calc.avg_daily_consumption * (calc.lead_time_days + calc.safety_stock_days))
                        AS reorder_point_qty,
                    GREATEST(
                        (calc.avg_daily_consumption * (calc.lead_time_days + calc.safety_stock_days))
                        - calc.onhand_qty,
                        0.0
                    ) AS qty_to_order,
                    CASE WHEN calc.avg_daily_consumption > 0 THEN
                        (calc.onhand_qty
                         - calc.avg_daily_consumption * (calc.lead_time_days + calc.safety_stock_days))
                        / calc.avg_daily_consumption
                    ELSE NULL END AS days_until_reorder,
                    CASE WHEN calc.avg_daily_consumption > 0 THEN
                        (CURRENT_DATE + (
                            GREATEST(
                                (calc.onhand_qty
                                 - calc.avg_daily_consumption
                                   * (calc.lead_time_days + calc.safety_stock_days))
                                / calc.avg_daily_consumption,
                                0.0
                            )::text || ' days'
                        )::interval)::date
                    ELSE NULL END AS suggested_reorder_date,
                    CASE WHEN calc.onhand_qty <=
                        calc.avg_daily_consumption * (calc.lead_time_days + calc.safety_stock_days)
                    THEN true ELSE false END AS shortage_alert
                FROM calc
            )
        """ % {
            'table': self._table,
            'default_lead_time': DEFAULT_FABRIC_LEAD_TIME_DAYS,
            'default_safety_days': DEFAULT_FABRIC_SAFETY_STOCK_DAYS,
        }
        self.env.cr.execute(query)

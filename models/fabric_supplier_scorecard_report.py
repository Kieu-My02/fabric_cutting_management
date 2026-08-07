# -*- coding: utf-8 -*-
from odoo import fields, models, tools

# FR-16 (Nhóm 10): ngưỡng cảnh báo mặc định cho Thẻ điểm NCC. Không thêm
# field cấu hình riêng trên res.partner cho từng NCC (khác với FR-12/FR-13
# vốn có đặc thù theo TỪNG MÃ VẢI) — ở đây đánh giá tổng thể một NCC nên áp
# dụng chung 1 ngưỡng cho toàn hệ thống, tương tự DEFAULT_NORM_VARIANCE_THRESHOLD.
DEFAULT_QC_PASS_RATE_ALERT_THRESHOLD = 90.0    # % - dưới mức này -> cảnh báo chất lượng
DEFAULT_RETURN_RATE_ALERT_THRESHOLD = 10.0     # % - trên mức này -> cảnh báo đổi trả/lỗi


class FabricSupplierScorecardReport(models.Model):
    """(Thống kê tùy nhu cầu): Thẻ điểm đánh giá Nhà cung cấp
    (Supplier Scorecard) — mỗi dòng ứng với 1 Nhà cung cấp (res.partner) đã
    từng có PO xác nhận trong phạm vi module này, gộp lại 4 mảng đã có sẵn
    rải rác ở các báo cáo/field khác nhau nhưng chưa nơi nào NHÌN CHUNG một
    lượt cho từng NCC để so sánh/xếp hạng:

    1) Quy mô mua hàng: số PO đã xác nhận + tổng giá trị (purchase.order).
    2) Chất lượng: tỷ lệ cây vải PASS/FAIL QC (stock.lot.qc_state) + độ lệch
       yards đo tay so với quy đổi lý thuyết trung bình.
    3) Đổi trả: số lượng theo từng loại Đổi trả/Lãnh bù/Báo thiếu
       (fabric.return.request) — mở rộng chỉ số fabric_return_rate đã có
       trên res.partner (res_partner.py) thành 1 báo cáo dạng bảng, cho phép
       lọc/nhóm/pivot theo nhiều NCC cùng lúc thay vì chỉ xem trên form.
    4) Thời gian giao hàng: số ngày trung bình từ lúc đặt hàng (date_order)
       đến lúc move nguyên liệu thật sự về kho (stock.move.date, qua
       purchase_line_id) — ước lượng lại lead time THỰC TẾ, dùng để đối
       chiếu với lead time KHAI BÁO trên mã hàng (FR-13, fabric_lead_time_days).

    Cũng là model report dạng SQL VIEW (_auto=False), cùng tinh thần với các
    báo cáo khác trong module: không tạo bảng dữ liệu riêng, không yêu cầu
    nhập liệu thêm, chỉ tổng hợp lại dữ liệu nghiệp vụ đã có sẵn."""

    _name = 'fabric.supplier.scorecard.report'
    _description = 'Thẻ điểm đánh giá Nhà cung cấp (Thống kê tùy nhu cầu)'
    _auto = False
    _order = 'supplier_risk_alert desc, defect_return_rate desc'

    partner_id = fields.Many2one('res.partner', string='Nhà cung cấp', readonly=True)
    company_id = fields.Many2one('res.company', string='Công ty', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Tiền tệ', readonly=True)

    po_count = fields.Integer(string='Số PO đã xác nhận', readonly=True)
    po_amount_total = fields.Monetary(
        string='Tổng giá trị đã mua', readonly=True, currency_field='currency_id')

    lot_count = fields.Integer(string='Số cây vải đã nhập', readonly=True)
    qc_pass_count = fields.Integer(string='Số cây PASS QC', readonly=True)
    qc_fail_count = fields.Integer(string='Số cây FAIL QC', readonly=True)
    qc_pending_count = fields.Integer(string='Số cây chờ QC', readonly=True)
    qc_pass_rate = fields.Float(
        string='Tỷ lệ PASS QC (%)', readonly=True, digits=(12, 2),
        help='= Số cây PASS QC / Tổng số cây vải đã QC (PASS hoặc FAIL, không tính '
             'cây đang Chờ kiểm) x 100.')
    avg_yards_variance_percent = fields.Float(
        string='TB |% lệch yards| (FR-07)', readonly=True, digits=(12, 2),
        help='Trung bình trị tuyệt đối % lệch giữa yards đo tay và yards quy đổi lý '
             'thuyết (chỉ tính các cây đã đo tay) — NCC có định lượng/khổ vải thực tế '
             'không ổn định sẽ có chỉ số này cao.')

    shortage_return_count = fields.Integer(string='Số lần Thiếu vải/Lãnh bù', readonly=True)
    defect_return_count = fields.Integer(string='Số lần Vải lỗi (Đổi trả NCC)', readonly=True)
    excess_return_count = fields.Integer(string='Số lần Trả vải dư', readonly=True)
    total_return_count = fields.Integer(string='Tổng số phiếu Đổi trả/Lãnh bù', readonly=True)
    defect_return_rate = fields.Float(
        string='Tỷ lệ PO bị đổi trả do lỗi (%)', readonly=True, digits=(12, 2),
        help='= Số PO có phát sinh yêu cầu Vải lỗi / Tổng số PO đã xác nhận x 100. '
             'Cùng công thức với res.partner.fabric_return_rate, xem tại form NCC.')

    avg_lead_time_days = fields.Float(
        string='Lead time thực tế TB (ngày)', readonly=True, digits=(12, 1),
        help='Số ngày trung bình từ lúc đặt hàng (Ngày đặt hàng) đến lúc move nguyên '
             'liệu tương ứng thật sự hoàn tất nhập kho — để đối chiếu với lead time '
             'KHAI BÁO trên mã hàng dùng cho Dự báo thiếu vải (FR-13).')

    supplier_risk_alert = fields.Boolean(
        string='Cảnh báo rủi ro NCC', readonly=True,
        help='Bật khi Tỷ lệ PASS QC dưới %s%% VÀ/HOẶC Tỷ lệ PO bị đổi trả do lỗi '
             'vượt %s%% — chỉ tính khi đã có đủ dữ liệu QC/PO để tránh cảnh báo sai '
             'cho NCC mới, ít giao dịch.' % (
                 DEFAULT_QC_PASS_RATE_ALERT_THRESHOLD, DEFAULT_RETURN_RATE_ALERT_THRESHOLD))

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %(table)s AS (
                WITH po_agg AS (
                    SELECT
                        po.partner_id AS partner_id,
                        po.company_id AS company_id,
                        COUNT(*) AS po_count,
                        SUM(po.amount_total) AS po_amount_total,
                        MAX(po.currency_id) AS currency_id
                    FROM purchase_order po
                    WHERE po.partner_id IS NOT NULL
                      AND po.state IN ('purchase', 'done')
                    GROUP BY po.partner_id, po.company_id
                ),
                lot_agg AS (
                    SELECT
                        sl.fabric_supplier_id AS partner_id,
                        sl.company_id AS company_id,
                        COUNT(*) AS lot_count,
                        COUNT(*) FILTER (WHERE sl.qc_state = 'pass') AS qc_pass_count,
                        COUNT(*) FILTER (WHERE sl.qc_state = 'fail') AS qc_fail_count,
                        COUNT(*) FILTER (WHERE sl.qc_state = 'pending') AS qc_pending_count,
                        AVG(ABS(sl.yards_variance_percent))
                            FILTER (WHERE sl.actual_yards != 0.0
                                    AND sl.actual_yards_theoretical != 0.0) AS avg_yards_variance_percent
                    FROM stock_lot sl
                    WHERE sl.fabric_supplier_id IS NOT NULL
                    GROUP BY sl.fabric_supplier_id, sl.company_id
                ),
                return_agg AS (
                    SELECT
                        fr.partner_id AS partner_id,
                        fr.company_id AS company_id,
                        COUNT(*) FILTER (WHERE fr.request_type = 'shortage') AS shortage_return_count,
                        COUNT(*) FILTER (WHERE fr.request_type = 'defect') AS defect_return_count,
                        COUNT(*) FILTER (WHERE fr.request_type = 'excess') AS excess_return_count,
                        COUNT(*) AS total_return_count
                    FROM fabric_return_request fr
                    WHERE fr.partner_id IS NOT NULL
                      AND fr.state != 'cancel'
                    GROUP BY fr.partner_id, fr.company_id
                ),
                lead_time_agg AS (
                    SELECT
                        po.partner_id AS partner_id,
                        po.company_id AS company_id,
                        AVG(EXTRACT(EPOCH FROM (sm.date - po.date_order)) / 86400.0)
                            AS avg_lead_time_days
                    FROM stock_move sm
                    JOIN purchase_order_line pol ON pol.id = sm.purchase_line_id
                    JOIN purchase_order po ON po.id = pol.order_id
                    WHERE sm.state = 'done'
                      AND po.state IN ('purchase', 'done')
                    GROUP BY po.partner_id, po.company_id
                )
                SELECT
                    po_agg.partner_id AS id,
                    po_agg.partner_id AS partner_id,
                    po_agg.company_id AS company_id,
                    po_agg.currency_id AS currency_id,
                    po_agg.po_count AS po_count,
                    po_agg.po_amount_total AS po_amount_total,
                    COALESCE(lot_agg.lot_count, 0) AS lot_count,
                    COALESCE(lot_agg.qc_pass_count, 0) AS qc_pass_count,
                    COALESCE(lot_agg.qc_fail_count, 0) AS qc_fail_count,
                    COALESCE(lot_agg.qc_pending_count, 0) AS qc_pending_count,
                    CASE WHEN COALESCE(lot_agg.qc_pass_count, 0) + COALESCE(lot_agg.qc_fail_count, 0) > 0
                        THEN lot_agg.qc_pass_count::numeric
                             / (lot_agg.qc_pass_count + lot_agg.qc_fail_count) * 100.0
                    ELSE NULL END AS qc_pass_rate,
                    COALESCE(lot_agg.avg_yards_variance_percent, 0.0) AS avg_yards_variance_percent,
                    COALESCE(return_agg.shortage_return_count, 0) AS shortage_return_count,
                    COALESCE(return_agg.defect_return_count, 0) AS defect_return_count,
                    COALESCE(return_agg.excess_return_count, 0) AS excess_return_count,
                    COALESCE(return_agg.total_return_count, 0) AS total_return_count,
                    CASE WHEN po_agg.po_count > 0
                        THEN COALESCE(return_agg.defect_return_count, 0)::numeric
                             / po_agg.po_count * 100.0
                    ELSE 0.0 END AS defect_return_rate,
                    lead_time_agg.avg_lead_time_days AS avg_lead_time_days,
                    CASE WHEN
                        (COALESCE(lot_agg.qc_pass_count, 0) + COALESCE(lot_agg.qc_fail_count, 0)) > 0
                        AND lot_agg.qc_pass_count::numeric
                            / (lot_agg.qc_pass_count + lot_agg.qc_fail_count) * 100.0
                            < %(qc_threshold)s
                    THEN true
                    WHEN po_agg.po_count > 0
                        AND COALESCE(return_agg.defect_return_count, 0)::numeric
                            / po_agg.po_count * 100.0 > %(return_threshold)s
                    THEN true
                    ELSE false END AS supplier_risk_alert
                FROM po_agg
                LEFT JOIN lot_agg ON lot_agg.partner_id = po_agg.partner_id
                    AND lot_agg.company_id = po_agg.company_id
                LEFT JOIN return_agg ON return_agg.partner_id = po_agg.partner_id
                    AND return_agg.company_id = po_agg.company_id
                LEFT JOIN lead_time_agg ON lead_time_agg.partner_id = po_agg.partner_id
                    AND lead_time_agg.company_id = po_agg.company_id
            )
        """ % {
            'table': self._table,
            'qc_threshold': DEFAULT_QC_PASS_RATE_ALERT_THRESHOLD,
            'return_threshold': DEFAULT_RETURN_RATE_ALERT_THRESHOLD,
        })
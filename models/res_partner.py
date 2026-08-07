# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    """Chỉ số đánh giá NCC nhắc tới trong 4.9.1.3 / SWOT: 'Xây dựng dữ liệu
    đánh giá nhà cung cấp lâu dài' - tỷ lệ số PO phát sinh đổi trả/lãnh bù
    trên tổng số PO đã mua của từng nhà cung cấp.

    Nhóm 8 - Cọc mua vải: mỗi NCC có một chính sách % cọc mặc định KHÁC
    NHAU tuỳ theo mức độ tin cậy/thoả thuận (không có một tỷ lệ chung 30%
    áp dụng cho tất cả NCC như giả định ban đầu). Khai báo tại đây để
    purchase.order lấy làm giá trị đề xuất ban đầu (deposit_percent), thu
    mua vẫn có thể sửa tay cho từng đơn cụ thể trước khi xác nhận.
    """

    _inherit = 'res.partner'

    fabric_deposit_percent = fields.Float(
        string='% Cọc mặc định (mua vải)',
        help='Tỷ lệ %% cọc mặc định áp dụng khi tạo Đơn mua vải cho NCC này '
             '(ví dụ NCC quen 20%%, NCC mới hợp tác 50%%...). Đây chỉ là giá '
             'trị GỢI Ý ban đầu cho purchase.order.deposit_percent - Thu mua '
             'vẫn có thể điều chỉnh riêng cho từng đơn trước khi xác nhận. '
             'Để 0 nếu NCC này không yêu cầu đặt cọc.',
    )

    @api.constrains('fabric_deposit_percent')
    def _check_fabric_deposit_percent(self):
        for partner in self:
            if not (0.0 <= partner.fabric_deposit_percent <= 100.0):
                raise ValidationError(_(
                    '%% Cọc mặc định (mua vải) phải trong khoảng 0-100%% (NCC: %s).'
                ) % partner.display_name)

    fabric_return_count = fields.Integer(
        string='Số lần đổi trả vải', compute='_compute_fabric_return_rate',
    )
    fabric_return_rate = fields.Float(
        string='Tỷ lệ đơn hàng bị đổi trả (%)', compute='_compute_fabric_return_rate',
        help='Số PO có phát sinh yêu cầu đổi trả (loại Vải lỗi) / Tổng số PO đã xác nhận.',
    )

    def _compute_fabric_return_rate(self):
        PurchaseOrder = self.env['purchase.order']
        FabricReturn = self.env['fabric.return.request']
        for partner in self:
            total_po = PurchaseOrder.search_count([
                ('partner_id', '=', partner.id), ('state', 'in', ('purchase', 'done')),
            ])
            defect_returns = FabricReturn.search([
                ('partner_id', '=', partner.id), ('request_type', '=', 'defect'),
                ('state', '!=', 'cancel'),
            ])
            partner.fabric_return_count = len(defect_returns)
            partner.fabric_return_rate = (len(defect_returns) / total_po * 100.0) if total_po else 0.0

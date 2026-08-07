# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FabricShortageWizard(models.TransientModel):
    """Nhánh 'Báo xuất thiếu' mô tả trong đồ án: khi không đủ vải đáp ứng
    tiến độ sản xuất, nhân viên phải báo trước 15-21 ngày, áp dụng đồng nhất
    cho tất cả khách hàng. Wizard này chặn cứng luật nghiệp vụ đó thay vì để
    nhân viên tự nhớ deadline."""

    _name = 'fabric.shortage.wizard'
    _description = 'Wizard Báo xuất thiếu vải'

    picking_id = fields.Many2one('stock.picking', string='Phiếu liên quan')
    purchase_order_id = fields.Many2one('purchase.order', string='PO liên quan')
    partner_id = fields.Many2one('res.partner', string='Nhà cung cấp')
    product_id = fields.Many2one('product.product', required=True, string='Loại vải thiếu')
    quantity = fields.Float(required=True, string='Số lượng thiếu (yards)')
    expected_date = fields.Date(string='Ngày cần bổ sung', required=True)
    reason = fields.Text(string='Lý do thiếu')

    @api.onchange('picking_id')
    def _onchange_picking_id(self):
        if self.picking_id and self.picking_id.origin:
            po = self.env['purchase.order'].search([('name', '=', self.picking_id.origin)], limit=1)
            if po:
                self.purchase_order_id = po
                self.partner_id = po.partner_id

    def action_create_report(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        days_notice = (self.expected_date - today).days
        if days_notice < 15:
            raise UserError(_(
                'Theo quy định hiện hành, báo xuất thiếu phải thực hiện trước '
                '15-21 ngày. Ngày cần bổ sung bạn chọn chỉ còn %s ngày.'
            ) % days_notice)
        request = self.env['fabric.return.request'].create({
            'request_type': 'shortage',
            'product_id': self.product_id.id,
            'quantity': self.quantity,
            'reason': self.reason,
            'expected_date': self.expected_date,
            'purchase_order_id': self.purchase_order_id.id,
            'partner_id': self.partner_id.id,
        })
        request.action_confirm()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'fabric.return.request',
            'view_mode': 'form',
            'res_id': request.id,
        }

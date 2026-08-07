# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class PurchaseOrderLine(models.Model):
    """FR-01: liên kết kỹ thuật PO với Lệnh Cắt (Cut Ticket), khai báo mã màu
    đặt hàng và đính kèm thẻ mẫu màu (Swatch Card) làm căn cứ đối chiếu.
    FR-02: cảnh báo khi số lượng đặt vượt định mức - so nhu cầu thực tế của
    Lệnh Cắt liên kết cộng tỷ lệ hao hụt cho phép Phòng Sample đã khai báo
    trên product.template (default_fabric_waste_percent, xem FR-07).
    """

    _inherit = 'purchase.order.line'

    production_id = fields.Many2one(
        'mrp.production', string='Lệnh Cắt (Cut Ticket) liên quan',
        domain=[('state', 'not in', ('done', 'cancel'))],
        help='FR-01: căn cứ kỹ thuật để đối chiếu số lượng đặt mua với nhu cầu thực tế.',
    )
    fabric_color_code = fields.Char(string='Mã màu đặt hàng')
    swatch_card_image = fields.Image(
        string='Thẻ mẫu màu (Swatch Card)', max_width=512, max_height=512,
        help='FR-01: ảnh chụp/scan thẻ mẫu màu do Phòng Sample cung cấp, '
             'làm căn cứ đối chiếu màu khi nhận hàng.',
    )
    fabric_demand_qty = fields.Float(
        string='Nhu cầu theo Lệnh Cắt', compute='_compute_fabric_demand_qty',
        help='Tổng nhu cầu nguyên liệu (theo BoM) của Lệnh Cắt liên kết, cho cùng sản phẩm.',
    )

    @api.depends('production_id', 'product_id')
    def _compute_fabric_demand_qty(self):
        for line in self:
            demand = 0.0
            if line.production_id and line.product_id:
                demand = sum(
                    line.production_id.move_raw_ids
                    .filtered(lambda m: m.product_id == line.product_id)
                    .mapped('product_uom_qty')
                )
            line.fabric_demand_qty = demand

    @api.onchange('product_qty', 'production_id', 'product_id')
    def _onchange_check_fabric_waste_allowance(self):
        for line in self:
            if not line.production_id or not line.product_id:
                continue
            demand = line.fabric_demand_qty
            if not demand:
                continue
            waste_percent = line.product_id.product_tmpl_id.default_fabric_waste_percent
            allowed_max = demand * (1 + (waste_percent or 0) / 100.0)
            if line.product_qty > allowed_max:
                return {
                    'warning': {
                        'title': _('Cảnh báo vượt định mức'),
                        'message': _(
                            'Số lượng đặt (%(ordered).2f %(uom)s) vượt quá nhu cầu Lệnh Cắt '
                            '"%(mo)s" cộng tỷ lệ hao hụt cho phép %(waste)s%% '
                            '(tối đa %(allowed).2f %(uom)s). Vui lòng kiểm tra lại với Phòng Sample.'
                        ) % {
                            'ordered': line.product_qty,
                            'uom': line.product_uom_id.name,
                            'mo': line.production_id.name,
                            'waste': waste_percent,
                            'allowed': allowed_max,
                        },
                    }
                }
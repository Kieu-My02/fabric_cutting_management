# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FabricReturnRequest(models.Model):
    """Số hóa 3 nhánh nghiệp vụ phát sinh mô tả ở mục 1.2.2 'Giai đoạn 5' của
    đồ án:
      1) Lãnh vải bù   (request_type = shortage)  - thiếu vải trong cây / vải lỗi phát hiện tại xưởng
      2) Báo xuất thiếu (request_type = shortage, tạo từ wizard riêng, có ràng buộc 15-21 ngày)
      3) Vải lỗi do NCC (request_type = defect)   - đổi trả với nhà cung cấp
      4) Trả vải về kho (request_type = excess)   - vải dư > 3 yards hoặc vải lỗi

    Mỗi yêu cầu có thể sinh ra một Purchase Order bổ sung (case 1, 3) hoặc một
    stock.picking nhập kho (case 4), giữ nguyên liên kết để truy vết đầy đủ -
    đúng mục tiêu 'tăng khả năng truy vết cây vải theo đơn hàng' của đề tài.
    """

    _name = 'fabric.return.request'
    _description = 'Yêu cầu Đổi trả / Lãnh bù / Báo thiếu vải'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Mã phiếu', default=lambda self: _('Mới'), copy=False, readonly=True)
    request_type = fields.Selection(
        [
            ('shortage', 'Thiếu vải trong cây / Lãnh bù'),
            ('defect', 'Vải lỗi do Nhà cung cấp'),
            ('excess', 'Vải dư - Trả về kho'),
        ],
        required=True, string='Loại yêu cầu', tracking=True,
    )
    partner_id = fields.Many2one('res.partner', string='Nhà cung cấp', tracking=True)
    purchase_order_id = fields.Many2one('purchase.order', string='Đơn mua liên quan')
    lot_id = fields.Many2one('stock.lot', string='Cây vải liên quan (Roll)')
    product_id = fields.Many2one('product.product', string='Loại vải', required=True)
    quantity = fields.Float(string='Số lượng (yards)', required=True)
    product_uom_id = fields.Many2one(
        'uom.uom', string='Đơn vị tính',
        default=lambda self: self.env.ref('uom.product_uom_yard', raise_if_not_found=False),
    )
    reason = fields.Text(string='Lý do / Mô tả')
    requested_date = fields.Date(string='Ngày yêu cầu', default=fields.Date.context_today)
    expected_date = fields.Date(string='Ngày cần xử lý xong')
    state = fields.Selection(
        [
            ('draft', 'Nháp'),
            ('confirmed', 'Đã xác nhận'),
            ('supplier_notified', 'Đã báo Nhà cung cấp'),
            ('resolved', 'Hoàn tất'),
            ('cancel', 'Đã hủy'),
        ],
        default='draft', tracking=True, string='Trạng thái',
    )
    replenishment_po_id = fields.Many2one('purchase.order', string='PO bổ sung', readonly=True, copy=False)
    return_picking_id = fields.Many2one('stock.picking', string='Phiếu nhập trả kho', readonly=True, copy=False)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Mới')) == _('Mới'):
                vals['name'] = self.env['ir.sequence'].next_by_code('fabric.return.request') or _('Mới')
        return super().create(vals_list)

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_notify_supplier(self):
        for rec in self:
            if not rec.partner_id:
                raise UserError(_('Cần chọn Nhà cung cấp trước khi gửi thông báo.'))
            rec.message_post(body=_(
                'Đã gửi thông báo %s đến nhà cung cấp %s cho %s %s.'
            ) % (dict(rec._fields['request_type'].selection).get(rec.request_type),
                 rec.partner_id.name, rec.quantity, rec.product_uom_id.name or ''))
        self.write({'state': 'supplier_notified'})

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_create_replenishment_po(self):
        """Nhánh 1 & 3: Lãnh vải bù / Đổi trả vải lỗi -> tạo PO bổ sung ngay
        trên cùng luồng, thay vì gọi điện/email thủ công như quy trình AS-IS."""
        self.ensure_one()
        if self.request_type not in ('shortage', 'defect'):
            raise UserError(_('Chỉ tạo PO bổ sung cho yêu cầu Thiếu vải hoặc Vải lỗi.'))
        if not self.partner_id:
            raise UserError(_('Cần chọn Nhà cung cấp trước khi tạo PO bổ sung.'))
        po = self.env['purchase.order'].create({
            'partner_id': self.partner_id.id,
            'origin': self.name,
            'order_line': [(0, 0, {
                'product_id': self.product_id.id,
                'product_qty': self.quantity,
                'product_uom_id': self.product_uom_id.id or self.product_id.uom_po_id.id,
                'price_unit': self.product_id.standard_price,
                'name': _('Bổ sung theo yêu cầu %s') % self.name,
            })],
        })
        self.write({'replenishment_po_id': po.id, 'state': 'resolved'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': po.id,
        }

    def action_create_return_picking(self):
        """Nhánh 4: Vải dư > 3 yards - Nhân viên theo tổ thực hiện thao tác
        trả vải chính thức trên hệ thống (tương ứng Bước cuối trong quy trình
        AS-IS mô tả ở mục 1.2.2)."""
        self.ensure_one()
        if self.request_type != 'excess':
            raise UserError(_('Chỉ tạo phiếu trả kho cho yêu cầu Vải dư.'))
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.company_id.id)], limit=1)
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'), ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not picking_type:
            raise UserError(_('Không tìm thấy loại phiếu nhập kho cho công ty này.'))
        move_vals = {
            'name': self.product_id.name,
            'product_id': self.product_id.id,
            'product_uom_qty': self.quantity,
            'product_uom': self.product_uom_id.id or self.product_id.uom_id.id,
            'location_id': picking_type.default_location_src_id.id or self.env.ref('stock.stock_location_customers').id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        }
        if self.lot_id:
            move_vals['lot_ids'] = [(4, self.lot_id.id)]
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': move_vals['location_id'],
            'location_dest_id': move_vals['location_dest_id'],
            'origin': self.name,
            'move_ids': [(0, 0, move_vals)],
        })
        self.write({'return_picking_id': picking.id, 'state': 'resolved'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
        }

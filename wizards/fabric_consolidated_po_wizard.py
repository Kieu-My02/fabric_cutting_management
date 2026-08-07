# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FabricConsolidatedPOWizard(models.TransientModel):
    """Nhóm 4: Gộp nhiều đơn cùng mã vải/màu/sản phẩm -> 1 PO tổng.

    Mở rộng 2 mảnh logic đã có sẵn trong module thay vì viết lại từ đầu:
      - Cách tính "tồn khả dụng hiện tại" (chỉ tính cây vải đã QC PASS, loại
        trừ Khu chờ QC) đã dùng trong mrp_production.action_suggest_fabric_rolls
        và fabric_shortage_forecast_report (FR-13).
      - Cách tạo Purchase Order bổ sung đã dùng trong
        fabric_return.action_create_replenishment_po - ở đây mở rộng để một
        PO có thể gồm NHIỀU dòng (nhiều mã vải/màu) thay vì chỉ một dòng.

    Nghiệp vụ: Thu mua chọn nhiều Lệnh Cắt (mrp.production) và/hoặc Đơn hàng
    bán (sale.order) đang cần CÙNG (một hoặc nhiều) mã vải/màu - thay vì phải
    tạo riêng lẻ từng PO cho từng đơn như quy trình AS-IS, hệ thống tự cộng
    dồn tổng nhu cầu theo từng mã vải/màu (product.product - mỗi biến thể là
    một màu), trừ đi tồn khả dụng hiện có, rồi sinh ra PO (gộp theo Nhà cung
    cấp - thông thường cùng 1 NCC cho cùng mã vải/màu nên ra đúng 1 PO).
    """

    _name = 'fabric.consolidated.po.wizard'
    _description = 'Nhóm 4: Gộp nhiều Lệnh Cắt/Đơn hàng cùng mã vải/màu -> 1 PO tổng'

    production_ids = fields.Many2many(
        'mrp.production', string='Lệnh Cắt (Cut Ticket)',
        domain=[('state', 'not in', ('cancel',))],
        help='Các Lệnh Cắt đang cần vải, sẽ được cộng dồn nhu cầu theo từng mã vải/màu.',
    )
    sale_order_ids = fields.Many2many(
        'sale.order', string='Đơn hàng bán',
        domain=[('state', '!=', 'cancel')],
        help='Các Đơn hàng bán đang cần vải (dùng lại Nhu cầu vải theo màu - FR-14). '
             'Nếu đơn chưa bấm "Tính nhu cầu vải theo màu" hệ thống sẽ tự tính khi gộp.',
    )
    line_ids = fields.One2many(
        'fabric.consolidated.po.wizard.line', 'wizard_id', string='Nhu cầu gộp theo mã vải/màu',
    )
    line_count = fields.Integer(compute='_compute_line_count', string='Số mã vải/màu')
    total_qty_to_order = fields.Float(
        compute='_compute_line_count', string='Tổng SL cần đặt mua',
        digits='Product Unit of Measure',
    )

    @api.depends('line_ids', 'line_ids.qty_to_order')
    def _compute_line_count(self):
        for wizard in self:
            wizard.line_count = len(wizard.line_ids)
            wizard.total_qty_to_order = sum(wizard.line_ids.mapped('qty_to_order'))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids') or []
        if active_model == 'mrp.production' and 'production_ids' in fields_list:
            res['production_ids'] = [(6, 0, active_ids)]
        elif active_model == 'sale.order' and 'sale_order_ids' in fields_list:
            res['sale_order_ids'] = [(6, 0, active_ids)]
        return res

    # ------------------------------------------------------------------
    # Bước 1: cộng dồn nhu cầu theo mã vải/màu, trừ tồn hiện có
    # ------------------------------------------------------------------
    def _get_fabric_moves_of_production(self, production):
        """Tiêu chí nhận diện dòng nguyên liệu là VẢI - dùng thống nhất với
        FR-08 (mrp_production.py) và FR-14 (sale_order.py): theo dõi bằng Lot
        và có cờ is_fabric trên product.template (không còn dùng
        default_fabric_gsm > 0 làm proxy)."""
        return production.move_raw_ids.filtered(
            lambda m: m.state not in ('done', 'cancel')
            and m.product_id.tracking == 'lot'
            and m.product_id.product_tmpl_id.is_fabric
        )

    def _get_onhand_qty(self, product):
        """Tồn khả dụng hiện tại của một mã vải/màu: chỉ tính cây vải đã QC
        PASS (hoặc không theo dõi Lot), loại trừ hẳn Khu chờ QC - cùng cách
        tính đã dùng ở mrp_production.action_suggest_fabric_rolls và
        fabric_shortage_forecast_report.py (FR-13)."""
        quarantine = self.env.ref(
            'fabric_cutting_management.location_qc_quarantine', raise_if_not_found=False)
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal'),
        ])
        quants = quants.filtered(
            lambda q: (not q.lot_id or q.lot_id.qc_state == 'pass')
            and (not quarantine or q.location_id != quarantine)
        )
        return sum(quants.mapped('quantity'))

    def _get_default_partner(self, product):
        """Gợi ý NCC: ưu tiên NCC chính khai báo trên mã vải (seller_ids),
        nếu chưa khai báo thì lấy NCC của lần mua gần nhất mã vải này."""
        seller = product.seller_ids[:1]
        if seller:
            return seller.partner_id
        last_line = self.env['purchase.order.line'].search(
            [('product_id', '=', product.id)], order='id desc', limit=1)
        return last_line.order_id.partner_id if last_line else self.env['res.partner']

    def _get_default_price_unit(self, product, partner):
        seller = product.seller_ids.filtered(lambda s: not partner or s.partner_id == partner)[:1]
        if seller:
            return seller.price
        return product.standard_price

    def action_compute_lines(self):
        """Nút 'Cộng dồn nhu cầu': tổng hợp lại toàn bộ line_ids từ
        production_ids + sale_order_ids hiện đang chọn trên wizard."""
        self.ensure_one()
        if not self.production_ids and not self.sale_order_ids:
            raise UserError(_(
                'Vui lòng chọn ít nhất một Lệnh Cắt hoặc Đơn hàng bán cùng mã vải/màu '
                'cần gộp trước khi cộng dồn nhu cầu.'
            ))

        demands = {}  # product_id -> {'product', 'qty', 'uom', 'sources': set()}

        for production in self.production_ids:
            for move in self._get_fabric_moves_of_production(production):
                already_done = sum(move.move_line_ids.filtered(
                    lambda l: l.picked).mapped('quantity'))
                remaining = move.product_uom_qty - already_done
                if remaining <= 0:
                    continue
                entry = demands.setdefault(move.product_id.id, {
                    'product': move.product_id, 'qty': 0.0,
                    'uom': move.product_uom, 'sources': set(),
                })
                entry['qty'] += remaining
                entry['sources'].add(production.display_name)

        for order in self.sale_order_ids:
            if not order.fabric_need_ids:
                order.action_compute_fabric_requirement()
            for need in order.fabric_need_ids:
                entry = demands.setdefault(need.product_id.id, {
                    'product': need.product_id, 'qty': 0.0,
                    'uom': need.uom_id, 'sources': set(),
                })
                entry['qty'] += need.qty_needed
                entry['sources'].add(order.display_name)

        if not demands:
            raise UserError(_(
                'Không tìm thấy nhu cầu vải nào trên các Lệnh Cắt/Đơn hàng đã chọn - '
                'kiểm tra lại các dòng nguyên liệu vải (theo dõi bằng Lot, có Định lượng '
                'chuẩn GSM) hoặc bấm "Tính nhu cầu vải theo màu (FR-14)" trên đơn hàng trước.'
            ))

        self.line_ids.unlink()
        line_vals = []
        for data in demands.values():
            product = data['product']
            onhand = self._get_onhand_qty(product)
            qty_to_order = max(data['qty'] - onhand, 0.0)
            partner = self._get_default_partner(product)
            line_vals.append((0, 0, {
                'product_id': product.id,
                'uom_id': data['uom'].id,
                'source_display': ', '.join(sorted(data['sources'])),
                'qty_needed': data['qty'],
                'onhand_qty': onhand,
                'qty_to_order': qty_to_order,
                'partner_id': partner.id if partner else False,
                'price_unit': self._get_default_price_unit(product, partner),
            }))
        self.line_ids = line_vals
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'fabric.consolidated.po.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ------------------------------------------------------------------
    # Bước 2: sinh PO tổng (mở rộng fabric_return.action_create_replenishment_po
    # để hỗ trợ nhiều dòng/nhiều mã vải trên cùng 1 PO)
    # ------------------------------------------------------------------
    def action_create_purchase_orders(self):
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.qty_to_order > 0)
        if not lines:
            raise UserError(_(
                'Không có mã vải/màu nào còn thiếu sau khi trừ tồn khả dụng hiện có - '
                'không cần tạo PO.'
            ))
        if any(not line.partner_id for line in lines):
            raise UserError(_(
                'Vui lòng chọn Nhà cung cấp cho tất cả các dòng cần đặt mua trước khi tạo PO.'
            ))

        origin = ', '.join(
            self.production_ids.mapped('display_name') + self.sale_order_ids.mapped('display_name')
        )

        partners = lines.mapped('partner_id')
        created_pos = self.env['purchase.order']
        for partner in partners:
            partner_lines = lines.filtered(lambda l: l.partner_id == partner)
            po = self.env['purchase.order'].create({
                'partner_id': partner.id,
                'origin': origin,
                'order_line': [(0, 0, {
                    'product_id': line.product_id.id,
                    'product_qty': line.qty_to_order,
                    'product_uom_id': line.uom_id.id or line.product_id.uom_po_id.id,
                    'price_unit': line.price_unit,
                    'name': _('Gộp nhu cầu (Nhóm 4) từ: %s') % (line.source_display or origin),
                }) for line in partner_lines],
            })
            created_pos |= po

        note = _('Đã tạo PO gộp %s từ yêu cầu gộp nhu cầu vải theo mã vải/màu (Nhóm 4).') % (
            ', '.join(created_pos.mapped('name')))
        for production in self.production_ids:
            production.message_post(body=note)
        for order in self.sale_order_ids:
            order.message_post(body=note)

        action = {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
        }
        if len(created_pos) == 1:
            action.update({'view_mode': 'form', 'res_id': created_pos.id})
        else:
            action.update({'view_mode': 'list,form', 'domain': [('id', 'in', created_pos.ids)]})
        return action


class FabricConsolidatedPOWizardLine(models.TransientModel):
    _name = 'fabric.consolidated.po.wizard.line'
    _description = 'Nhóm 4: Dòng nhu cầu gộp theo mã vải/màu'

    wizard_id = fields.Many2one(
        'fabric.consolidated.po.wizard', string='Wizard', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Mã vải/màu', required=True, readonly=True)
    product_tmpl_id = fields.Many2one(
        related='product_id.product_tmpl_id', string='Mã hàng (vải)', readonly=True)
    uom_id = fields.Many2one('uom.uom', string='Đơn vị tính', required=True)
    source_display = fields.Char(
        string='Từ Lệnh Cắt/Đơn hàng', readonly=True,
        help='Danh sách các Lệnh Cắt/Đơn hàng đã đóng góp vào nhu cầu gộp này.')
    qty_needed = fields.Float(
        string='Tổng nhu cầu (gộp)', readonly=True, digits='Product Unit of Measure',
        help='Tổng nhu cầu của mã vải/màu này, cộng dồn từ tất cả Lệnh Cắt/Đơn hàng đã chọn.')
    onhand_qty = fields.Float(
        string='Tồn khả dụng hiện có', readonly=True, digits='Product Unit of Measure',
        help='Chỉ tính cây vải đã QC PASS, không tính hàng còn ở Khu chờ QC.')
    qty_to_order = fields.Float(
        string='SL cần đặt mua', digits='Product Unit of Measure',
        help='Mặc định = MAX(Tổng nhu cầu - Tồn khả dụng, 0). Có thể chỉnh tay trước khi tạo PO.')
    partner_id = fields.Many2one(
        'res.partner', string='Nhà cung cấp', domain=[('supplier_rank', '>', 0)],
        help='Các dòng cùng Nhà cung cấp sẽ được gộp chung vào 1 PO.')
    price_unit = fields.Float(string='Đơn giá')

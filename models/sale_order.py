# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class SaleOrderFabricNeed(models.Model):
    """FR-14: 1 dòng = tổng nhu cầu của MỘT mã vải cụ thể (mỗi biến thể sản
    phẩm coi như một màu riêng, đúng cách Odoo quản lý biến thể/Variant) cho
    toàn bộ đơn hàng - được (re)tính bởi nút "Tính nhu cầu vải theo màu" trên
    sale.order. Lưu lại (không phải view) để nhân viên kinh doanh xem lại
    được ngay cả sau khi rời trang, không cần bấm lại nút mỗi lần mở đơn."""

    _name = 'sale.order.fabric.need'
    _description = 'FR-14: Nhu cầu vải theo màu của đơn hàng'
    _order = 'product_id'

    order_id = fields.Many2one('sale.order', string='Đơn hàng', required=True, ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', string='Loại vải (màu)', required=True,
        help='Mỗi biến thể sản phẩm vải (Variant) trên BoM tương ứng một màu cụ thể.')
    product_tmpl_id = fields.Many2one(
        related='product_id.product_tmpl_id', string='Mã hàng (vải)', store=True)
    uom_id = fields.Many2one('uom.uom', string='Đơn vị tính', required=True)
    qty_needed = fields.Float(
        string='Tổng nhu cầu vải', digits='Product Unit of Measure',
        help='Tổng nhu cầu vải nổ ra từ BoM của tất cả các dòng sản phẩm trên đơn hàng '
             'dùng mã vải/màu này, quy đổi theo Đơn vị tính của mã vải.')

    # Nhóm 9: tổng vải THỰC TẾ đã xuất dùng cho đơn hàng may này, cộng dồn
    # theo đúng mã vải/màu (product_id) từ move nguyên liệu ("picked") của
    # TẤT CẢ Lệnh Cắt (mrp.production) sinh ra từ đơn hàng - qua liên kết
    # order_id.production_ids (Nhóm 5-6, mrp_production.py). Không stored vì
    # phụ thuộc vào production_ids - quan hệ suy ra (computed) từ dây chuyền
    # MTO, không phải one2many lưu trữ ổn định để depends() theo dõi chính
    # xác qua nhiều bước quan hệ; tính lại mỗi lần đọc cho luôn khớp thực tế,
    # khác với qty_needed (chỉ đổi khi bấm lại nút FR-14, có chủ đích lưu
    # lại giữa các lần xem).
    qty_issued = fields.Float(
        string='Thực tế đã xuất ', compute='_compute_qty_issued',
        digits='Product Unit of Measure',
        help='Tổng số lượng thực tế đã xuất dùng (move nguyên liệu đã "picked") cho mã '
             'vải này, cộng dồn từ tất cả Lệnh Cắt sinh ra từ đơn hàng.')
    qty_variance = fields.Float(
        string='Chênh lệch TT - KH', compute='_compute_qty_issued', digits='Product Unit of Measure',
        help='Thực tế đã xuất trừ Tổng nhu cầu vải. Âm = xuất chưa tới nhu cầu (còn phải '
             'xuất tiếp/PO); dương = đã xuất vượt nhu cầu lý thuyết.')
    qty_variance_percent = fields.Float(
        string='% Chênh lệch', compute='_compute_qty_issued', digits=(12, 2))

    @api.depends(
        'qty_needed', 'product_id', 'order_id.production_ids.state',
        'order_id.production_ids.move_raw_ids.product_id',
        'order_id.production_ids.move_raw_ids.state',
        'order_id.production_ids.move_raw_ids.move_line_ids.quantity',
        'order_id.production_ids.move_raw_ids.move_line_ids.picked',
    )
    def _compute_qty_issued(self):
        for need in self:
            moves = need.order_id.production_ids.move_raw_ids.filtered(
                lambda m: m.product_id == need.product_id and m.state != 'cancel')
            issued = sum(moves.move_line_ids.filtered(lambda l: l.picked).mapped('quantity'))
            need.qty_issued = issued
            need.qty_variance = issued - need.qty_needed
            need.qty_variance_percent = (
                (need.qty_variance / need.qty_needed * 100.0) if need.qty_needed else 0.0)


class SaleOrder(models.Model):
    """FR-14: Nhập mã đơn hàng -> tính vải cần. Dùng thẳng cơ chế định mức
    (mrp.bom) đã có sẵn của Odoo: với mỗi dòng đơn hàng, tìm BoM tương ứng
    của sản phẩm, nổ định mức (kể cả BoM dạng Kit/Phantom lồng nhau) ra danh
    sách nguyên liệu, rồi chỉ giữ lại các dòng là VẢI - theo đúng tiêu chí đã
    dùng nhất quán trong toàn bộ module này (theo dõi bằng Lot và có khai báo
    Định lượng chuẩn GSM trên product.template, xem thêm mrp_production.py
    FR-08 và fabric_norm_variance_report.py FR-12) - rồi gộp tổng theo từng
    mã vải/màu (product.product)."""

    _inherit = 'sale.order'

    fabric_need_ids = fields.One2many(
        'sale.order.fabric.need', 'order_id', string='Nhu cầu vải theo màu', readonly=True)
    fabric_need_count = fields.Integer(
        string='Số mã vải cần', compute='_compute_fabric_need_count')

    # Nhóm 5-6: các Lệnh Cắt (mrp.production) suy ra được là phục vụ đơn hàng
    # này - xem thêm mrp_production.py (sale_order_id, tính theo dây chuyền
    # MTO chuẩn của Odoo). Dùng để hiển thị nhanh tiến độ SX ngay trên đơn
    # hàng, và là nguồn dữ liệu cho báo cáo tổng hợp (Insights).
    production_ids = fields.One2many(
        'mrp.production', 'sale_order_id', string='Lệnh Cắt liên quan ', readonly=True)

    # Nhóm 5-6: tổng hợp tình trạng thanh toán từ account.move.payment_state
    # của các hoá đơn bán hàng ĐÃ VÀO SỔ (posted) sinh ra từ đơn hàng này.
    # Đây là bản tổng hợp ĐƠN GIẢN HOÁ cho mục đích dashboard/cảnh báo của đồ
    # án - không thay thế cho nghiệp vụ đối soát công nợ đầy đủ của phân hệ
    # Kế toán (account.move vẫn là nguồn dữ liệu gốc, đáng tin cậy duy nhất).
    payment_state = fields.Selection(
        [
            ('not_invoiced', 'Chưa xuất hoá đơn'),
            ('not_paid', 'Chưa thanh toán'),
            ('in_payment', 'Đang xử lý thanh toán'),
            ('partial', 'Thanh toán một phần'),
            ('paid', 'Đã thanh toán'),
            ('reversed', 'Đã bị đảo (Reversed)'),
        ],
        string='Tình trạng thanh toán', compute='_compute_payment_state', store=True)
    amount_invoiced = fields.Monetary(
        string='Đã xuất hoá đơn', compute='_compute_payment_state', store=True,
        currency_field='currency_id')
    amount_residual = fields.Monetary(
        string='Còn phải thu', compute='_compute_payment_state', store=True,
        currency_field='currency_id')

    @api.depends(
        'order_line.invoice_lines.move_id.payment_state',
        'order_line.invoice_lines.move_id.amount_residual',
        'order_line.invoice_lines.move_id.amount_total',
        'order_line.invoice_lines.move_id.state',
        'order_line.invoice_lines.move_id.move_type',
    )
    def _compute_payment_state(self):
        for order in self:
            invoices = order.order_line.invoice_lines.move_id.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
            if not invoices:
                order.payment_state = 'not_invoiced'
                order.amount_invoiced = 0.0
                order.amount_residual = 0.0
                continue

            order.amount_invoiced = sum(invoices.mapped('amount_total'))
            order.amount_residual = sum(invoices.mapped('amount_residual'))
            states = set(invoices.mapped('payment_state'))

            # Thứ tự ưu tiên: một khi ĐÃ có hoá đơn nào "một phần" (partial),
            # hoặc tổng còn phải thu nằm giữa 0 và tổng đã xuất hoá đơn (vd.
            # hoá đơn A đã trả đủ, hoá đơn B chưa trả gì) -> coi cả đơn hàng
            # là "một phần", vì khách vẫn còn nợ nhưng đã trả một phần giá trị.
            if 'partial' in states or (
                    0 < order.amount_residual < order.amount_invoiced):
                order.payment_state = 'partial'
            elif states == {'paid'}:
                order.payment_state = 'paid'
            elif states == {'reversed'}:
                order.payment_state = 'reversed'
            elif 'in_payment' in states:
                order.payment_state = 'in_payment'
            else:
                order.payment_state = 'not_paid'

    # Nhóm 7: các Đơn mua (PO) đã đặt vải phục vụ Lệnh Cắt của đơn hàng này -
    # suy ra qua PO Line -> Lệnh Cắt (purchase_order_line.production_id, xem
    # FR-01) rồi ngược lại Lệnh Cắt -> Đơn hàng bán (production_ids ở trên).
    # Không phân biệt PO tạo tay hay PO tổng hợp từ wizard Gộp PO (Nhóm 4) -
    # cả 2 đều đi qua production_id như nhau.
    purchase_order_ids = fields.Many2many(
        'purchase.order', string='Đơn mua liên quan',
        compute='_compute_purchase_order_ids',
        help='Các Đơn mua đã đặt vải phục vụ Lệnh Cắt của đơn hàng này.')
    purchase_order_count = fields.Integer(
        string='Số đơn mua', compute='_compute_purchase_order_ids')
    purchase_payment_state = fields.Selection(
        [
            ('none', 'Không có đơn mua'),
            ('not_billed', 'Chưa nhận hoá đơn'),
            ('not_paid', 'Chưa thanh toán'),
            ('in_payment', 'Đang xử lý thanh toán'),
            ('partial', 'Thanh toán một phần'),
            ('paid', 'Đã thanh toán'),
            ('reversed', 'Đã bị đảo (Reversed)'),
            ('mixed', 'Nhiều tình trạng khác nhau'),
        ],
        string='Tình trạng thanh toán mua ', compute='_compute_purchase_order_ids',
        help='Gộp nhanh payment_state của tất cả đơn mua '
             'liên quan. "Nhiều tình trạng khác nhau" nghĩa là các PO đang ở các mức thanh '
             'toán khác nhau - cần mở từng PO để xem chi tiết.')

    # Nhóm 8: các phiếu "xuất kho nội bộ cho Phòng Cắt" (is_fabric_release,
    # xem stock_picking.py) phát sinh từ move nguyên liệu vải của các Lệnh
    # Cắt liên quan - tức khâu ĐƯA vải từ kho chính ra Phòng Cắt để bắt đầu
    # cắt. KHÔNG phải phiếu giao thành phẩm cho khách hàng (đã có sẵn smart
    # button "Giao hàng" chuẩn của module sale_stock, không cần làm lại).
    fabric_release_picking_ids = fields.Many2many(
        'stock.picking', string='Phiếu xuất vải cho Phòng Cắt ',
        compute='_compute_fabric_release_pickings')
    fabric_release_count = fields.Integer(
        string='Số phiếu xuất vải', compute='_compute_fabric_release_pickings')
    fabric_outbound_state = fields.Selection(
        [
            ('pending', 'Chưa xuất vải'),
            ('partial', 'Đang xuất một phần'),
            ('done', 'Đã xuất xong'),
        ],
        string='Tình trạng xuất vải', compute='_compute_fabric_release_pickings')

    production_count = fields.Integer(
        string='Số Lệnh Cắt', compute='_compute_production_count')

    @api.depends('production_ids')
    def _compute_production_count(self):
        for order in self:
            order.production_count = len(order.production_ids)

    @api.depends('production_ids')
    def _compute_purchase_order_ids(self):
        PurchaseLine = self.env['purchase.order.line']
        for order in self:
            lines = PurchaseLine.search([
                ('production_id', 'in', order.production_ids.ids),
                ('order_id.state', '!=', 'cancel'),
            ]) if order.production_ids else PurchaseLine
            purchase_orders = lines.order_id
            order.purchase_order_ids = purchase_orders
            order.purchase_order_count = len(purchase_orders)
            if not purchase_orders:
                order.purchase_payment_state = 'none'
            else:
                states = set(purchase_orders.mapped('payment_state'))
                order.purchase_payment_state = states.pop() if len(states) == 1 else 'mixed'

    @api.depends(
        'production_ids.move_raw_ids.picking_id',
        'production_ids.move_raw_ids.picking_id.state',
        'production_ids.move_raw_ids.picking_id.is_fabric_release',
    )
    def _compute_fabric_release_pickings(self):
        for order in self:
            pickings = order.production_ids.move_raw_ids.picking_id.filtered(
                lambda p: p.is_fabric_release and p.state != 'cancel')
            order.fabric_release_picking_ids = pickings
            order.fabric_release_count = len(pickings)
            if not pickings:
                order.fabric_outbound_state = 'pending'
            elif all(p.state == 'done' for p in pickings):
                order.fabric_outbound_state = 'done'
            else:
                order.fabric_outbound_state = 'partial'

    def action_view_productions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('mrp.mrp_production_action')
        action['domain'] = [('id', 'in', self.production_ids.ids)]
        return action

    def action_view_purchase_orders(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_form_action')
        action['domain'] = [('id', 'in', self.purchase_order_ids.ids)]
        return action

    def action_view_fabric_release_pickings(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('stock.action_picking_tree_all')
        action['domain'] = [('id', 'in', self.fabric_release_picking_ids.ids)]
        return action

    @api.depends('fabric_need_ids')
    def _compute_fabric_need_count(self):
        for order in self:
            order.fabric_need_count = len(order.fabric_need_ids)

    def _find_bom_for_product(self, product):
        """Tìm mrp.bom phù hợp nhất cho 1 sản phẩm: ưu tiên BoM khai báo
        riêng cho đúng biến thể (product_id), nếu không có mới lấy BoM
        chung cho cả mã hàng (product_id để trống - áp dụng mọi biến thể).
        Không dùng phương thức nội bộ _bom_find (private, có thể đổi chữ ký
        giữa các bản Odoo) - tự truy vấn trực tiếp cho rõ ràng, dễ bảo trì."""
        return self.env['mrp.bom'].search([
            ('product_tmpl_id', '=', product.product_tmpl_id.id),
            '|', ('product_id', '=', product.id), ('product_id', '=', False),
        ], order='product_id desc, sequence, id', limit=1)

    def _explode_bom_fabric_needs(self, bom, qty_in_bom_uom, needs, skipped=None):
        """Đệ quy nổ định mức: qty_in_bom_uom là số lượng thành phẩm của
        chính `bom` này (tính theo bom.product_uom_id) cần sản xuất. Với mỗi
        dòng định mức: nếu là BoM con dạng Kit/Phantom (child_bom_id) thì nổ
        tiếp xuống dòng con; nếu là mã vải (theo tiêu chí chung của module)
        thì cộng dồn vào `needs`, quy đổi về Đơn vị tính của chính mã vải đó.
        Bỏ qua nguyên phụ liệu khác (chỉ, khuy, nhãn...) không phải vải.

        `skipped` (tuỳ chọn): list để ghi lại lý do từng dòng nguyên liệu bị
        loại (không thoả tiêu chí mã vải) - phục vụ chẩn đoán khi FR-14 không
        ra kết quả nào, giúp nhân viên biết chính xác cần sửa gì thay vì chỉ
        có 1 thông báo chung chung."""
        if not bom or qty_in_bom_uom <= 0:
            return
        factor = qty_in_bom_uom / (bom.product_qty or 1.0)
        for line in bom.bom_line_ids:
            line_qty = line.product_qty * factor  # theo line.product_uom_id
            if line.child_bom_id and line.child_bom_id.type == 'phantom':
                qty_for_child = line.product_uom_id._compute_quantity(
                    line_qty, line.child_bom_id.product_uom_id)
                self._explode_bom_fabric_needs(line.child_bom_id, qty_for_child, needs, skipped)
                continue

            product = line.product_id
            tmpl = product.product_tmpl_id
            if product.tracking != 'lot' or not tmpl.is_fabric:
                if skipped is not None:
                    reasons = []
                    if product.tracking != 'lot':
                        tracking_label = dict(
                            product._fields['tracking']._description_selection(self.env)
                        ).get(product.tracking, product.tracking)
                        reasons.append(_('chưa theo dõi bằng Lot (đang là "%s")') % tracking_label)
                    if not tmpl.is_fabric:
                        reasons.append(_('chưa được đánh dấu "Là mã Vải" (FR-07)'))
                    # Ghi kèm ID sản phẩm/mẫu SP: Reference (default_code) không bắt
                    # buộc duy nhất trong Odoo, nên nếu tồn tại 2 sản phẩm trùng tên/
                    # trùng mã, người dùng sẽ không phân biệt được record nào đang bị
                    # đọc bởi dòng BoM này (vd: sửa nhầm 1 sản phẩm trùng tên khác,
                    # tick "Là mã Vải" xong nhưng lỗi vẫn không hết). Kèm ID giúp mở
                    # thẳng đúng record qua URL /odoo/inventory/products/<id>.
                    skipped.append(_(
                        '"%(name)s" [SP ID: %(product_id)s, Mẫu SP ID: %(tmpl_id)s]: %(reasons)s'
                    ) % {
                        'name': product.display_name,
                        'product_id': product.id,
                        'tmpl_id': tmpl.id,
                        'reasons': ', '.join(reasons),
                    })
                continue  # không phải mã vải (theo tiêu chí chung của module) -> bỏ qua

            qty_in_product_uom = line.product_uom_id._compute_quantity(line_qty, product.uom_id)
            entry = needs.setdefault(product.id, {'product': product, 'qty': 0.0})
            entry['qty'] += qty_in_product_uom

    def action_compute_fabric_requirement(self):
        """FR-14: nút chính - tính lại toàn bộ nhu cầu vải theo màu cho đơn
        hàng hiện tại, dựa trên SL từng dòng sản phẩm x BoM tương ứng."""
        for order in self:
            needs = {}
            # Ghi lại vấn đề của TỪNG dòng sản phẩm riêng biệt (không phải 1
            # câu chung cho cả đơn) - để nhân viên biết chính xác dòng nào
            # đang thiếu BoM, và với dòng có BoM thì nguyên liệu nào bị loại
            # vì lý do gì (thiếu tracking Lot hay thiếu GSM).
            line_issues = []
            for line in order.order_line.filtered(
                    lambda l: not l.display_type and l.product_id and l.product_uom_qty):
                bom = order._find_bom_for_product(line.product_id)
                if not bom:
                    line_issues.append(_(
                        '- "%s": chưa có Bill of Materials (BoM) khai báo cho sản phẩm này.'
                    ) % line.product_id.display_name)
                    continue

                needs_before = {pid: dict(data) for pid, data in needs.items()}
                skipped = []
                qty_in_bom_uom = line.product_uom_id._compute_quantity(
                    line.product_uom_qty, bom.product_uom_id)
                order._explode_bom_fabric_needs(bom, qty_in_bom_uom, needs, skipped)

                # Dòng này có đóng góp thêm nhu cầu vải mới không? So sánh
                # trước/sau vì needs dùng chung cho cả đơn (nhiều dòng có thể
                # cộng dồn vào cùng 1 mã vải).
                contributed = any(
                    pid not in needs_before or needs[pid]['qty'] != needs_before[pid]['qty']
                    for pid in needs
                )
                if not contributed:
                    if skipped:
                        line_issues.append(_(
                            '- "%(product)s": có BoM (%(bom)s) nhưng không nguyên liệu nào '
                            'đạt tiêu chí mã vải:\n   %(details)s'
                        ) % {
                            'product': line.product_id.display_name,
                            'bom': bom.display_name,
                            'details': '\n   '.join(skipped),
                        })
                    else:
                        line_issues.append(_(
                            '- "%(product)s": có BoM (%(bom)s) nhưng BoM không có dòng '
                            'nguyên liệu nào (rỗng).'
                        ) % {'product': line.product_id.display_name, 'bom': bom.display_name})

            order.fabric_need_ids.unlink()
            if needs:
                order.fabric_need_ids = [(0, 0, {
                    'product_id': data['product'].id,
                    'uom_id': data['product'].uom_id.id,
                    'qty_needed': data['qty'],
                }) for data in needs.values()]

            if not needs:
                detail = '\n'.join(line_issues) if line_issues else ''
                order.message_post(body=_(
                    'FR-14: không tìm thấy nhu cầu vải nào cho đơn hàng này.%s'
                ) % (('\n' + detail) if detail else ''))
            elif line_issues:
                # Thành công một phần: có mã vải được tính, nhưng vẫn có
                # dòng sản phẩm khác bị bỏ qua - trước đây bị im lặng bỏ
                # qua hoàn toàn, không có cảnh báo nào cho người dùng biết.
                order.message_post(body=_(
                    'FR-14: đã tính nhu cầu vải, nhưng có %(count)d dòng sản phẩm bị bỏ '
                    'qua (không tính vào nhu cầu vải):\n%(detail)s'
                ) % {'count': len(line_issues), 'detail': '\n'.join(line_issues)})
        return True

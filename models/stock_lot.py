# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

YARD_IN_METERS = 0.9144
# FR-07: ngưỡng lệch cho phép giữa yards đo tay và yards quy đổi lý thuyết
# trước khi hệ thống cảnh báo cho công nhân xả vải / tổ trưởng biết để đối chiếu lại.
FABRIC_YARD_VARIANCE_THRESHOLD = 3.0  # %


class StockLot(models.Model):
    """Mỗi bản ghi stock.lot ở đây đại diện cho MỘT cây vải (Fabric Roll).
    Dùng Lot/Serial number tracking có sẵn của Odoo, chỉ mở rộng thêm các
    thuộc tính đặc thù ngành dệt may mà nhóm đã khảo sát ở Chương II.9."""

    _inherit = 'stock.lot'

    roll_sequence = fields.Char(
        string='Số Roll',
        help='Số thứ tự cây vải do nhà cung cấp hoặc kho gán, in trên tem barcode.',
    )
    fabric_width = fields.Float(string='Khổ vải (cm)')
    fabric_color_code = fields.Char(string='Mã màu / Ánh màu')
    dye_lot_number = fields.Char(
        string='Số Lot nhuộm', index=True,
        help='Mã mẻ nhuộm từ NCC — các cây cùng mã màu nhưng khác Lot nhuộm '
             'có thể bị lệch màu, không được trải chung bàn cắt.',
    )
    fabric_gsm = fields.Float(
        string='Định lượng vải (g/m²)',
        help='Định lượng (khối lượng trên 1 m² vải) do NCC công bố hoặc QC đo lại. '
             'Là căn cứ để hệ thống tự quy đổi giữa Kg và Yards.',
    )
    actual_yards = fields.Float(
        string='Số yards thực tế (đo tay)',
        help='Số yards do công nhân xả vải trực tiếp đo được trên cây vải.',
    )
    actual_weight_kg = fields.Float(
        string='Số ký thực tế (cân)',
        help='Số ký do công nhân xả vải trực tiếp cân được trên cây vải.',
    )
    actual_yards_theoretical = fields.Float(
        string='Yards quy đổi (theo Kg)', compute='_compute_actual_yards_theoretical',
        store=True,
        help='FR-07: Yards tính ngược từ số ký thực tế, khổ vải và định lượng (GSM), '
             'dùng làm căn cứ đối chiếu với số yards đo tay.',
    )
    yards_variance_percent = fields.Float(
        string='% Lệch yards', compute='_compute_actual_yards_theoretical',
        store=True,
        help='Chênh lệch giữa Yards đo tay và Yards quy đổi lý thuyết. '
             'Vượt ngưỡng %s%% sẽ được cảnh báo trên giao diện.' % FABRIC_YARD_VARIANCE_THRESHOLD,
    )
    yards_variance_alert = fields.Boolean(
        string='Cảnh báo lệch số liệu', compute='_compute_actual_yards_theoretical', store=True,
    )

    @api.depends('actual_weight_kg', 'actual_yards', 'fabric_width', 'fabric_gsm')
    def _compute_actual_yards_theoretical(self):
        for lot in self:
            theoretical = 0.0
            if lot.actual_weight_kg and lot.fabric_width and lot.fabric_gsm:
                width_m = lot.fabric_width / 100.0
                area_m2 = lot.actual_weight_kg * 1000.0 / lot.fabric_gsm
                length_m = area_m2 / width_m if width_m else 0.0
                theoretical = length_m / YARD_IN_METERS
            lot.actual_yards_theoretical = theoretical

            variance = 0.0
            if theoretical and lot.actual_yards:
                variance = (lot.actual_yards - theoretical) / theoretical * 100.0
            lot.yards_variance_percent = variance
            lot.yards_variance_alert = abs(variance) > FABRIC_YARD_VARIANCE_THRESHOLD

    @api.onchange('actual_weight_kg', 'fabric_width', 'fabric_gsm')
    def _onchange_suggest_actual_yards(self):
        """FR-07: khi chưa đo tay (actual_yards=0), gợi ý luôn giá trị quy đổi
        lý thuyết để công nhân xả vải có số liệu tham chiếu ngay khi nhập ký."""
        for lot in self:
            if not lot.actual_yards and lot.actual_yards_theoretical:
                lot.actual_yards = lot.actual_yards_theoretical

    @api.onchange('product_id')
    def _onchange_product_suggest_fabric_specs(self):
        """FR-07: gợi ý GSM/khổ vải chuẩn theo mã hàng khi tạo Lot mới,
        chỉ điền khi đang trống - không ghi đè nếu QC đã đo lại số thực tế
        khác chuẩn của một lô cụ thể."""
        for lot in self:
            if lot.product_id:
                tmpl = lot.product_id.product_tmpl_id
                if not lot.fabric_gsm and tmpl.default_fabric_gsm:
                    lot.fabric_gsm = tmpl.default_fabric_gsm
                if not lot.fabric_width and tmpl.default_fabric_width:
                    lot.fabric_width = tmpl.default_fabric_width

    qc_state = fields.Selection(
        [
            ('pending', 'Chờ kiểm'),
            ('pass', 'PASS'),
            ('fail', 'FAIL'),
        ],
        string='Trạng thái QC', default='pending', tracking=True, index=True,
    )
    qc_note = fields.Text(string='Ghi chú QC')
    fabric_supplier_id = fields.Many2one(
        'res.partner', string='Nhà cung cấp',
        domain=[('supplier_rank', '>', 0)],
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Nhóm 10 - Thẻ điểm NCC: đảm bảo company_id luôn được set khi tạo
        cây vải, bất kể tạo qua form, qua nhập kho (stock.move.line), hay qua
        bất kỳ đường code nào khác của Odoo core. Thiếu field này khiến
        fabric_supplier_scorecard_report.py (view SQL) không JOIN được lot
        với purchase_order cùng NCC (điều kiện JOIN có so company_id), làm
        toàn bộ chỉ số QC (qc_pass_rate, qc_pass_count...) luôn hiện 0 dù dữ
        liệu QC thật sự đã có - xem thêm ghi chú tại
        stock_picking.py::_set_fabric_supplier_on_received_lots."""
        for vals in vals_list:
            if not vals.get('company_id'):
                vals['company_id'] = self.env.company.id
        return super().create(vals_list)

    qty_in_qc_quarantine = fields.Float(
        string='SL đang chờ ở Khu chờ QC', compute='_compute_qty_in_qc_quarantine',
        help='Số lượng của cây vải này hiện đang nằm ở vị trí "Khu chờ QC", '
             'chưa được trừ vào tồn khả dụng cho Lệnh Cắt.',
    )

    def _compute_qty_in_qc_quarantine(self):
        quarantine = self.env.ref(
            'fabric_cutting_management.location_qc_quarantine', raise_if_not_found=False)
        for lot in self:
            if not quarantine:
                lot.qty_in_qc_quarantine = 0.0
                continue
            lot.qty_in_qc_quarantine = sum(
                lot.quant_ids.filtered(lambda q: q.location_id == quarantine).mapped('quantity')
            )

    def _get_qc_release_picking_type(self):
        """Nhóm 1: lấy (hoặc tạo nếu chưa có) loại phiếu nội bộ dùng riêng cho
        thao tác 'giải phóng' cây vải khỏi Khu chờ QC vào kho chính, gắn với
        kho của công ty hiện hành. Tạo lazily bằng Python thay vì khai báo cứng
        trong XML vì stock.warehouse là dữ liệu theo từng công ty, không có
        external ID cố định để tham chiếu an toàn."""
        self.ensure_one()
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.company_id.id or self.env.company.id)], limit=1)
        if not warehouse:
            raise UserError(_('Không tìm thấy kho (Warehouse) nào cho công ty hiện tại.'))
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('warehouse_id', '=', warehouse.id),
            ('name', '=', 'Giải phóng QC'),
        ], limit=1)
        if not picking_type:
            quarantine = self.env.ref('fabric_cutting_management.location_qc_quarantine')
            sequence = self.env['ir.sequence'].sudo().create({
                'name': 'Giải phóng QC - %s' % warehouse.name,
                'prefix': 'QCR/%s/' % warehouse.code,
                'padding': 5,
                'company_id': warehouse.company_id.id,
            })
            picking_type = self.env['stock.picking.type'].sudo().create({
                'name': 'Giải phóng QC',
                'code': 'internal',
                'sequence_id': sequence.id,
                # Từ Odoo 19, sequence_code (Sequence Prefix) là field bắt
                # buộc trên stock.picking.type, kể cả khi đã tự set
                # sequence_id thủ công như trên. Không set field này sẽ báo
                # lỗi "Missing required value for the field 'Sequence
                # Prefix'". Vì sequence_id đã được set ở trên, Odoo sẽ KHÔNG
                # tự tạo thêm ir.sequence khác từ sequence_code này — giá
                # trị dưới đây chỉ để thoả điều kiện required.
                'sequence_code': 'QCR',
                'warehouse_id': warehouse.id,
                'default_location_src_id': quarantine.id,
                'default_location_dest_id': warehouse.lot_stock_id.id,
                'company_id': warehouse.company_id.id,
            })
        return picking_type

    def action_set_qc_pass(self):
        """Nhóm 1: khi PASS, tự động tạo + xác nhận một dịch chuyển nội bộ
        đưa toàn bộ số lượng cây vải đang ở "Khu chờ QC" vào kho chính
        (WH/Stock). Đây là lúc cây vải thật sự được trừ vào tồn khả dụng và
        đủ điều kiện để Lệnh Cắt đặt chỗ (reservation) — trước đó vị trí
        "Khu chờ QC" nằm ngoài cây vị trí mà Lệnh Cắt dùng để tìm nguyên liệu."""
        quarantine = self.env.ref('fabric_cutting_management.location_qc_quarantine')
        for lot in self:
            quants = lot.quant_ids.filtered(
                lambda q: q.location_id == quarantine and q.quantity > 0)
            qty = sum(quants.mapped('quantity'))
            if qty > 0:
                picking_type = lot._get_qc_release_picking_type()
                move = self.env['stock.move'].create({
                    'description_picking': _('Giải phóng QC: %s') % lot.name,
                    'product_id': lot.product_id.id,
                    'product_uom_qty': qty,
                    'product_uom': lot.product_id.uom_id.id,
                    'location_id': quarantine.id,
                    'location_dest_id': picking_type.default_location_dest_id.id,
                    'picking_type_id': picking_type.id,
                    'company_id': lot.company_id.id or self.env.company.id,
                })
                move._action_confirm()
                move._action_assign()
                if not move.move_line_ids:
                    move.move_line_ids = [(0, 0, {
                        'product_id': lot.product_id.id,
                        'lot_id': lot.id,
                        'quantity': qty,
                        'product_uom_id': lot.product_id.uom_id.id,
                        'location_id': quarantine.id,
                        'location_dest_id': picking_type.default_location_dest_id.id,
                    })]
                else:
                    move.move_line_ids.write({'lot_id': lot.id, 'quantity': qty})
                move.move_line_ids.write({'picked': True})
                move._action_done()
        self.write({'qc_state': 'pass'})
        self._auto_bill_purchase_orders_if_qc_ready()

    def _auto_bill_purchase_orders_if_qc_ready(self):
        """Nhóm 8 (bổ sung) - ngay khi QC Pass khiến một Đơn mua đạt
        is_qc_fully_passed=True và đơn đó CHƯA có hoá đơn hàng hoá chính
        thức nào (payment_state == 'not_billed'), tự động tạo (action_create_
        invoice - hàm lõi Odoo) và Post luôn hoá đơn NCC tương ứng. Mục đích:
        nút "Thanh toán phần còn lại cho NCC" trên form PO khả dụng NGAY,
        không bắt Thu mua/Kế toán phải tự tay sang phân hệ khác bấm Create
        Bill rồi Post trước khi trả tiền được. Chỉ tác động các PO thực sự
        liên quan tới (các) lot vừa PASS trong self, không quét toàn hệ
        thống."""
        move_lines = self.env['stock.move.line'].search([
            ('lot_id', 'in', self.ids),
            ('move_id.purchase_line_id', '!=', False),
            ('state', '=', 'done'),
        ])
        orders = move_lines.move_id.purchase_line_id.order_id
        for order in orders:
            if not order.is_qc_fully_passed or order.payment_state != 'not_billed':
                continue
            try:
                # Dùng savepoint riêng cho mỗi PO: nếu action_create_invoice
                # lỗi giữa chừng (thiếu cấu hình thuế/tài khoản...), chỉ
                # rollback phần thao tác của PO này, không kéo theo hỏng
                # transaction chung (và do đó hỏng luôn thao tác PASS QC
                # đang chạy cùng transaction).
                with self.env.cr.savepoint():
                    order.action_create_invoice()
            except Exception:
                # Không để lỗi tạo hoá đơn chặn đứng luôn thao tác PASS QC
                # của công nhân QC - hoá đơn vẫn có thể tạo tay sau đó như
                # luồng chuẩn của Odoo. Log lại để Thu mua/Kế toán/IT biết
                # vì sao hoá đơn không tự tạo, tránh im lặng khó debug khi demo.
                _logger.warning(
                    "Auto create bill thất bại cho PO %s (id=%s) sau khi "
                    "QC Pass - cần tạo Bill thủ công.",
                    order.name, order.id, exc_info=True,
                )
                continue
            draft_bills = order.invoice_ids.filtered(
                lambda m: m.move_type == 'in_invoice' and m.state == 'draft')
            if draft_bills:
                # action_create_invoice (lõi Odoo) đôi khi để invoice_date
                # trống - bắt buộc phải có thì Post mới không báo lỗi
                # "Bill/Refund date is required". Mặc định ngày hiện tại,
                # Kế toán vẫn sửa lại tay được sau đó nếu cần.
                no_date_bills = draft_bills.filtered(lambda m: not m.invoice_date)
                if no_date_bills:
                    no_date_bills.write({
                        'invoice_date': fields.Date.context_today(self),
                    })
                try:
                    with self.env.cr.savepoint():
                        draft_bills.action_post()
                except Exception:
                    _logger.warning(
                        "Auto post bill thất bại cho PO %s (id=%s) - hoá "
                        "đơn đã tạo ở trạng thái Draft, cần Post thủ công.",
                        order.name, order.id, exc_info=True,
                    )
                    continue

    def action_set_qc_fail(self):
        """Nhóm 1: nếu cây vải đã từng PASS và đã được chuyển vào kho chính,
        việc đánh dấu FAIL lại không được âm thầm bỏ qua phần tồn kho đã
        "giải phóng":
        - Nếu toàn bộ số lượng vẫn còn nguyên trong kho chính (chưa bị Lệnh
          Cắt nào tiêu thụ) -> tự động tạo dịch chuyển ngược lại Khu chờ QC.
        - Nếu đã bị tiêu thụ một phần/toàn bộ -> KHÔNG tự đảo ngược (không đủ
          hàng vật lý để thu hồi); chặn lại và yêu cầu xử lý qua nghiệp vụ
          Đổi trả/Lãnh bù (fabric.return.request) để giữ đúng vết truy xuất.
        """
        quarantine = self.env.ref('fabric_cutting_management.location_qc_quarantine')
        for lot in self:
            if lot.qc_state == 'pass':
                stock_quants = lot.quant_ids.filtered(
                    lambda q: q.location_id.usage == 'internal'
                    and q.location_id != quarantine and q.quantity > 0)
                qty_in_stock = sum(stock_quants.mapped('quantity'))
                # Đã có move nào tiêu thụ/xuất cây vải này ra khỏi kho nội bộ
                # chưa (dùng cho Lệnh Cắt hoặc xuất bán) - nếu có, không đủ cơ
                # sở để tự đảo ngược, phải xử lý thủ công qua Đổi trả/Lãnh bù.
                already_consumed = self.env['stock.move.line'].search_count([
                    ('lot_id', '=', lot.id),
                    ('location_id.usage', '=', 'internal'),
                    ('location_id', '!=', quarantine.id),
                    ('location_dest_id.usage', 'in', ('production', 'customer')),
                    ('state', '=', 'done'),
                ])
                if qty_in_stock <= 0:
                    pass  # không còn gì để hoàn - có thể đã tiêu thụ hết, cho phép set FAIL để ghi nhận lịch sử
                elif already_consumed:
                    raise UserError(_(
                        'Không thể chuyển cây vải "%s" về FAIL: cây vải đã PASS và một phần đã '
                        'được Lệnh Cắt tiêu thụ khỏi kho chính. Vui lòng tạo yêu cầu Đổi trả/Lãnh bù '
                        '(nhánh "Vải lỗi do Nhà cung cấp") thay vì đảo ngược trạng thái QC.'
                    ) % lot.display_name)
                else:
                    picking_type = lot._get_qc_release_picking_type()
                    move = self.env['stock.move'].create({
                        'description_picking': _('Thu hồi do FAIL lại: %s') % lot.name,
                        'product_id': lot.product_id.id,
                        'product_uom_qty': qty_in_stock,
                        'product_uom': lot.product_id.uom_id.id,
                        'location_id': picking_type.default_location_dest_id.id,
                        'location_dest_id': quarantine.id,
                        'picking_type_id': picking_type.id,
                        'company_id': lot.company_id.id or self.env.company.id,
                    })
                    move._action_confirm()
                    move._action_assign()
                    if not move.move_line_ids:
                        move.move_line_ids = [(0, 0, {
                            'product_id': lot.product_id.id,
                            'lot_id': lot.id,
                            'quantity': qty_in_stock,
                            'product_uom_id': lot.product_id.uom_id.id,
                            'location_id': picking_type.default_location_dest_id.id,
                            'location_dest_id': quarantine.id,
                        })]
                    else:
                        move.move_line_ids.write({'lot_id': lot.id, 'quantity': qty_in_stock})
                    move.move_line_ids.write({'picked': True})
                    move._action_done()
        self.write({'qc_state': 'fail'})

    # ------------------------------------------------------------------
    # FR-11: Truy vết real-time
    # Không tạo lại dữ liệu — chỉ mở view trên stock.move.line (đã ghi nhận
    # đầy đủ mọi lần cây vải này di chuyển: nhập kho từ PO, xuất cho Lệnh
    # Cắt, trả về kho...) và trên fabric.return.request đã lọc theo Lot.
    # ------------------------------------------------------------------
    move_line_count = fields.Integer(
        string='Số lượt di chuyển', compute='_compute_fabric_trace_counts',
    )
    fabric_return_count = fields.Integer(
        string='Số yêu cầu Đổi trả/Lãnh bù', compute='_compute_fabric_trace_counts',
    )
    cutting_production_count = fields.Integer(
        string='Số Lệnh Cắt đã dùng', compute='_compute_fabric_trace_counts',
    )

    def _compute_fabric_trace_counts(self):
        move_line_data = self.env['stock.move.line']._read_group(
            [('lot_id', 'in', self.ids)], ['lot_id'], ['__count'],
        )
        move_line_map = {lot.id: count for lot, count in move_line_data}

        return_data = self.env['fabric.return.request']._read_group(
            [('lot_id', 'in', self.ids)], ['lot_id'], ['__count'],
        )
        return_map = {lot.id: count for lot, count in return_data}

        production_data = self.env['stock.move.line']._read_group(
            [('lot_id', 'in', self.ids), ('move_id.raw_material_production_id', '!=', False)],
            ['lot_id', 'move_id.raw_material_production_id'], ['__count'],
        )
        production_map = {}
        for lot, _production, _count in production_data:
            production_map.setdefault(lot.id, set()).add(_production.id if _production else None)

        for lot in self:
            lot.move_line_count = move_line_map.get(lot.id, 0)
            lot.fabric_return_count = return_map.get(lot.id, 0)
            lot.cutting_production_count = len(production_map.get(lot.id, set()))

    def action_view_lot_traceability(self):
        """FR-11: toàn bộ lịch sử di chuyển real-time của cây vải — dùng
        thẳng traceability chuẩn stock.move.line có sẵn của Odoo, không cần
        model riêng: nhập từ PO, chuyển kho, xuất cho Lệnh Cắt, trả về kho...
        đều đã được Odoo ghi lại đầy đủ trên move line."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'stock.stock_move_line_action')
        action['domain'] = [('lot_id', '=', self.id)]
        action['name'] = _('Truy vết cây vải: %s') % self.display_name
        return action

    def action_view_lot_cutting_productions(self):
        """FR-11: các Lệnh Cắt (mrp.production) đã tiêu thụ cây vải này."""
        self.ensure_one()
        move_lines = self.env['stock.move.line'].search([
            ('lot_id', '=', self.id),
            ('move_id.raw_material_production_id', '!=', False),
        ])
        productions = move_lines.move_id.raw_material_production_id
        action = self.env['ir.actions.act_window']._for_xml_id('mrp.mrp_production_action')
        action['domain'] = [('id', 'in', productions.ids)]
        action['name'] = _('Lệnh Cắt đã dùng cây vải: %s') % self.display_name
        return action

    def action_view_lot_return_requests(self):
        """FR-11: các yêu cầu Đổi trả/Lãnh bù/Báo thiếu liên quan cây vải này."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'fabric_cutting_management.action_fabric_return_request')
        action['domain'] = [('lot_id', '=', self.id)]
        action['name'] = _('Đổi trả/Lãnh bù cây vải: %s') % self.display_name
        return action
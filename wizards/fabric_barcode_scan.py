# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FabricBarcodeScan(models.TransientModel):
    """Odoo Community không có sẵn app Barcode đầy đủ (chỉ có ở Enterprise),
    nên nhóm tự xây một wizard quét nhẹ, dùng chung field `barcode` có sẵn
    trên product/lot và module `barcodes` (barcode nomenclature) của
    Community. Wizard nhận input từ máy quét cầm tay hoặc điện thoại (hoạt
    động như bàn phím - gõ ký tự + Enter), tra cứu cây vải và THỰC THI trực
    tiếp 1 trong 3 thao tác mô tả ở mục 2.8.5 / 4.9.3 của đồ án — không chỉ
    kiểm tra và báo trạng thái, mà xác nhận luôn stock.move.line/stock.picking
    tương ứng, đúng tinh thần FR-05 'số hóa giao nhận bằng thiết bị ngoại vi'.
    """

    _name = 'fabric.barcode.scan'
    _description = 'Quét mã vạch cây vải'

    barcode = fields.Char(string='Mã quét', required=True)
    scan_type = fields.Selection(
        [
            ('receipt', 'Nhận vải vào kho (check-in)'),
            ('release', 'Xuất kho cho Phòng Cắt'),
            ('location', 'Tra cứu vị trí lưu trữ'),
        ],
        required=True, default='receipt', string='Loại thao tác',
    )
    lot_id = fields.Many2one('stock.lot', string='Cây vải', readonly=True)
    picking_id = fields.Many2one('stock.picking', string='Phiếu liên quan', readonly=True)
    result_message = fields.Char(string='Kết quả', readonly=True)

    def _find_lot(self):
        self.ensure_one()
        lot = self.env['stock.lot'].search([
            '|', ('name', '=', self.barcode), ('roll_sequence', '=', self.barcode),
        ], limit=1)
        if not lot:
            raise UserError(_('Không tìm thấy cây vải với mã quét: %s') % self.barcode)
        return lot

    def _find_move_line(self, lot, picking_domain):
        """Tìm dòng thao tác (stock.move.line) của cây vải này trên một phiếu
        kho đang chờ xử lý. Ưu tiên dòng đã được hệ thống reserve sẵn đúng lot
        này (trường hợp phổ biến khi phiếu đã được xác nhận từ trước); nếu
        chưa có (ví dụ nhập vải lần đầu, hệ thống chưa biết Roll ID cụ thể),
        tìm dòng cùng sản phẩm nhưng chưa gán lot để gán vào."""
        MoveLine = self.env['stock.move.line']

        already_done = MoveLine.search(
            picking_domain + [('lot_id', '=', lot.id), ('picked', '=', True)], limit=1,
        )
        if already_done:
            raise UserError(_(
                'Cây vải %s đã được xử lý trên phiếu %s trước đó — không thể quét lại.'
            ) % (lot.name, already_done.picking_id.name))

        domain = picking_domain + [('picked', '=', False)]

        line = MoveLine.search(domain + [('lot_id', '=', lot.id)], limit=1)
        if line:
            return line

        return MoveLine.search(
            domain + [('product_id', '=', lot.product_id.id), ('lot_id', '=', False)],
            limit=1, order='id',
        )

    def action_process_scan(self):
        self.ensure_one()
        lot = self._find_lot()
        self.lot_id = lot.id

        if self.scan_type == 'receipt':
            self._process_receipt(lot)
        elif self.scan_type == 'release':
            self._process_release(lot)
        else:  # location
            self._process_location(lot)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'fabric.barcode.scan',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def _process_receipt(self, lot):
        if lot.qc_state == 'fail':
            raise UserError(_('Cây vải %s đã bị đánh dấu FAIL, không thể nhận vào kho.') % lot.name)

        picking_domain = [
            ('picking_id.picking_type_id.code', '=', 'incoming'),
            ('picking_id.state', 'in', ('assigned', 'confirmed')),
        ]
        line = self._find_move_line(lot, picking_domain)
        if not line:
            raise UserError(_(
                'Không tìm thấy phiếu nhập kho nào đang chờ xử lý cho cây vải %s.\n'
                'Vui lòng kiểm tra Đơn mua hàng đã xác nhận và phiếu nhập kho tương ứng.'
            ) % lot.name)

        line.write({'lot_id': lot.id, 'picked': True})
        self.picking_id = line.picking_id.id

        remaining = line.picking_id.move_line_ids.filtered(lambda l: not l.picked)
        if not remaining:
            line.picking_id.button_validate()
            self.result_message = _(
                'Đã nhận cây vải %s vào kho và xác nhận hoàn tất phiếu nhập %s. '
                'Trạng thái QC: Chờ kiểm.'
            ) % (lot.name, line.picking_id.name)
        else:
            self.result_message = _(
                'Đã ghi nhận cây vải %s vào phiếu nhập %s. Còn %s dòng chưa quét trên phiếu này.'
            ) % (lot.name, line.picking_id.name, len(remaining))

    def _process_release(self, lot):
        if lot.qc_state != 'pass':
            raise UserError(_(
                'Cây vải %s chưa PASS QC (trạng thái hiện tại: %s). '
                'Không thể xuất kho cho Phòng Cắt.'
            ) % (lot.name, dict(lot._fields['qc_state'].selection).get(lot.qc_state)))

        picking_domain = [
            ('picking_id.is_fabric_release', '=', True),
            ('picking_id.state', 'in', ('assigned', 'confirmed')),
        ]
        line = self._find_move_line(lot, picking_domain)
        if not line:
            raise UserError(_(
                'Không tìm thấy phiếu cấp phát vải nào cho Phòng Cắt đang chờ xử lý '
                'với cây vải %s.'
            ) % lot.name)

        line.write({'lot_id': lot.id, 'picked': True})
        self.picking_id = line.picking_id.id

        remaining = line.picking_id.move_line_ids.filtered(lambda l: not l.picked)
        if not remaining:
            line.picking_id.button_validate()
            self.result_message = _(
                'Đã xuất kho cây vải %s và xác nhận hoàn tất phiếu %s cho Phòng Cắt.'
            ) % (lot.name, line.picking_id.name)
        else:
            self.result_message = _(
                'Đã quét cây vải %s trên phiếu %s. Còn %s dòng chưa quét trên phiếu này.'
            ) % (lot.name, line.picking_id.name, len(remaining))

    def _process_location(self, lot):
        quants = self.env['stock.quant'].search([
            ('lot_id', '=', lot.id), ('quantity', '>', 0),
        ])
        locations = ', '.join(quants.mapped('location_id.display_name')) or _('Không còn tồn kho')
        self.result_message = _('Vị trí hiện tại của cây vải %s: %s') % (lot.name, locations)
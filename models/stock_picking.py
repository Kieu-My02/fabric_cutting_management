# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockPicking(models.Model):
    """Đúng theo mô tả quy trình 3.3.5.3 trong đồ án: Bước 3 - nếu trạng thái
    QC là FAIL thì yêu cầu bị giữ lại, chưa được phép xuất kho. Ở đây ta biến
    quy tắc nghiệp vụ đó thành một ràng buộc cứng của hệ thống thay vì phụ
    thuộc vào việc nhân viên kho tự kiểm tra bằng mắt."""

    _inherit = 'stock.picking'

    is_fabric_release = fields.Boolean(
        string='Phiếu cấp phát vải cho Phòng Cắt',
        compute='_compute_is_fabric_release', store=True,
    )

    @api.depends('picking_type_id', 'picking_type_id.code', 'picking_type_id.name')
    def _compute_is_fabric_release(self):
        for picking in self:
            picking_type = picking.picking_type_id
            picking.is_fabric_release = bool(
                picking_type
                and picking_type.code == 'outgoing'
                and picking_type.default_location_src_id.usage == 'internal'
            )

    def button_validate(self):
        for picking in self:
            if picking.is_fabric_release:
                lots = picking.move_line_ids.lot_id
                failed_or_pending = lots.filtered(lambda l: l.qc_state != 'pass')
                if failed_or_pending:
                    raise UserError(_(
                        'Không thể xác nhận xuất kho.\n'
                        'Các cây vải sau chưa PASS QC (FAIL hoặc còn Chờ kiểm):\n%s\n\n'
                        'Vui lòng cập nhật trạng thái QC trên cây vải trước khi xuất kho '
                        'cho Phòng Cắt.'
                    ) % '\n'.join('- %s (%s)' % (l.name, dict(l._fields['qc_state'].selection).get(l.qc_state)) for l in failed_or_pending))
        result = super().button_validate()
        self._set_fabric_supplier_on_received_lots()
        return result

    def _set_fabric_supplier_on_received_lots(self):
        """Nhóm 10 - Thẻ điểm NCC: tự động điền stock.lot.fabric_supplier_id
        ngay khi phiếu nhập (receipt) từ Purchase Order được xác nhận xong,
        lấy trực tiếp từ NCC của move.purchase_line_id.order_id - không bắt
        nhân viên kho phải tự chọn tay field này trên từng lot như trước
        (khiến qc_pass_rate/qc_pass_count trên Thẻ điểm NCC luôn ra 0 vì
        không join được, dù QC đã PASS/FAIL thật trên stock.lot).
        Chỉ điền khi đang trống - không ghi đè nếu ai đó đã tự set khác
        (ví dụ trường hợp lãnh bù/đổi trả gán lại lot cho một NCC khác)."""
        for picking in self:
            if picking.picking_type_id.code != 'incoming':
                continue
            done_lines = picking.move_line_ids.filtered(
                lambda l: l.state == 'done'
                and l.lot_id
                and not l.lot_id.fabric_supplier_id
                and l.move_id.purchase_line_id
                and l.move_id.product_id.product_tmpl_id.is_fabric
            )
            for line in done_lines:
                line.lot_id.fabric_supplier_id = (
                    line.move_id.purchase_line_id.order_id.partner_id.id
                )

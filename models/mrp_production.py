# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    """FR-08: Lệnh Cắt (Cut Ticket) được ánh xạ trực tiếp vào Manufacturing
    Order (mrp.production) có sẵn của Odoo — không tạo model riêng, vì Odoo
    đã có đầy đủ cơ chế BoM (định mức vải theo mã hàng) + move_raw_ids (tiêu
    thụ nguyên liệu) đúng bản chất nghiệp vụ.

    Phần custom duy nhất: gợi ý cây vải (Roll) CÙNG Lot nhuộm cho nguyên liệu
    vải trong lệnh cắt — đúng quy tắc đã khảo sát ở Chương II: các cây cùng
    mã màu nhưng khác Lot nhuộm có thể lệch màu, không được trải chung bàn cắt.
    """

    _inherit = 'mrp.production'

    # Nhóm 5-6: mốc giai đoạn SX (Cắt -> May -> Hoàn thành). Công đoạn May
    # diễn ra ở khâu kế tiếp, ngoài phạm vi tồn kho mà module này theo dõi
    # (không có mrp.production/workorder riêng cho May trong đồ án) nên cần
    # cập nhật thủ công bằng nút bấm; riêng "Hoàn thành" luôn được đồng bộ tự
    # động theo trạng thái chuẩn của Lệnh Cắt (state = done), không cho phép
    # lệch pha giữa 2 field.
    production_stage = fields.Selection(
        [
            ('cutting', 'Đang cắt'),
            ('sewing', 'Đang may'),
            ('completed', 'Hoàn thành'),
        ],
        string='Giai đoạn SX (Nhóm 5-6)', default='cutting', copy=False, tracking=True,
        help='Cắt: đang thực hiện tại Phòng Cắt (mặc định). May: đã bàn giao bán '
             'thành phẩm sang chuyền may (cập nhật thủ công). Hoàn thành: tự động '
             'gán khi Lệnh Cắt chuyển trạng thái "Đã hoàn thành".')
    date_stage_sewing = fields.Datetime(
        string='Ngày chuyển sang May', readonly=True, copy=False)
    date_stage_completed = fields.Datetime(
        string='Ngày hoàn thành SX', readonly=True, copy=False)

    # Nhóm 5-6: suy ra Đơn hàng bán gốc theo dây chuyền MTO chuẩn của Odoo,
    # không tạo thêm quan hệ dữ liệu mới - chỉ đọc lại move_dest_ids đã có
    # sẵn (Lệnh Cắt -> move nguyên liệu -> ... -> move giao hàng có
    # sale_line_id). Lưu ý: từ Odoo 19, model procurement.group không còn áp
    # dụng cho luồng MTO nữa (thay bằng cơ chế stock.reference mới) nên
    # KHÔNG còn field procurement_group_id trên mrp.production - không dùng
    # phương án dự phòng qua đó như các bản Odoo cũ nữa.
    sale_order_id = fields.Many2one(
        'sale.order', string='Đơn hàng bán liên quan',
        compute='_compute_sale_order_id', store=True,
        help='Suy ra tự động từ dây chuyền MTO của Lệnh Cắt, dùng để đối chiếu '
             'tình trạng thanh toán trên báo cáo tổng hợp.')

    @api.depends('move_dest_ids.sale_line_id')
    def _compute_sale_order_id(self):
        for production in self:
            sale_order = production.move_dest_ids.sale_line_id.order_id[:1]
            production.sale_order_id = sale_order.id if sale_order else False

    def write(self, vals):
        res = super().write(vals)
        # Chỉ tự động đồng bộ theo CHIỀU TỚI (chưa completed -> completed) khi
        # Lệnh Cắt thật sự đã "done" - không tự ý lùi giai đoạn khi state đổi
        # khác đi, vì đó có thể là thao tác huỷ/mở lại cần người dùng tự xem
        # xét và cập nhật giai đoạn thủ công cho đúng thực tế.
        if vals.get('state') == 'done':
            to_complete = self.filtered(lambda p: p.production_stage != 'completed')
            if to_complete:
                to_complete.write({
                    'production_stage': 'completed',
                    'date_stage_completed': fields.Datetime.now(),
                })
        return res

    def action_mark_stage_sewing(self):
        """Nhóm 5-6: nút thủ công đánh dấu đã bàn giao bán thành phẩm sang
        chuyền may, không tác động trạng thái chuẩn (state) của Lệnh Cắt."""
        for production in self:
            if production.production_stage == 'cutting':
                production.write({
                    'production_stage': 'sewing',
                    'date_stage_sewing': fields.Datetime.now(),
                })

    def action_suggest_fabric_rolls(self):
        self.ensure_one()
        suggestions = []

        # Chỉ áp dụng cho dòng nguyên liệu là VẢI: theo dõi bằng Lot và có
        # cờ product.template.is_fabric được đánh dấu -> đây là cách phân
        # biệt "vải" với nguyên phụ liệu khác (chỉ, khuy, nhãn...). Không
        # còn dùng default_fabric_gsm > 0 làm proxy vì một mã vải thật sự
        # lỡ bỏ trống GSM sẽ bị loại nhầm khỏi gợi ý.
        fabric_moves = self.move_raw_ids.filtered(
            lambda m: m.product_id.tracking == 'lot'
            and m.product_id.product_tmpl_id.is_fabric
        )
        if not fabric_moves:
            raise UserError(_(
                'Lệnh cắt này không có nguyên liệu vải nào (theo dõi bằng Lot '
                'và được đánh dấu "Là mã Vải") để gợi ý.'
            ))

        for move in fabric_moves:
            needed_qty = move.product_uom_qty
            candidate_lots = self.env['stock.lot'].search([
                ('product_id', '=', move.product_id.id),
                ('qc_state', '=', 'pass'),
            ])

            # Nhóm các Roll theo Lot nhuộm, tính tổng tồn khả dụng mỗi nhóm
            quarantine = self.env.ref(
                'fabric_cutting_management.location_qc_quarantine', raise_if_not_found=False)
            groups = {}
            for lot in candidate_lots:
                # Nhóm 1: dù đã lọc qc_state == 'pass' ở trên, vẫn loại trừ rõ
                # ràng "Khu chờ QC" ở đây - phòng trường hợp dữ liệu lệch pha
                # (vd. import dữ liệu cũ, hoặc PASS nhưng move giải phóng lỗi)
                # để tránh gợi ý nhầm hàng chưa thật sự nằm ở kho khả dụng.
                available_qty = sum(
                    lot.quant_ids
                    .filtered(lambda q: q.location_id.usage == 'internal'
                              and (not quarantine or q.location_id != quarantine))
                    .mapped('quantity')
                )
                if available_qty <= 0:
                    continue
                key = lot.dye_lot_number or _('(Không rõ Lot nhuộm)')
                groups.setdefault(key, []).append((lot, available_qty))

            # Ưu tiên nhóm Lot nhuộm đầu tiên (theo ngày tạo Roll cũ nhất
            # trong nhóm - FIFO) đủ đáp ứng trọn vẹn nhu cầu, tránh phải
            # trộn nhiều Lot nhuộm khác nhau trên cùng bàn cắt.
            chosen = None
            for dye_lot, lots_qty in sorted(
                groups.items(),
                key=lambda kv: min(l.create_date for l, _q in kv[1]),
            ):
                total = sum(q for _l, q in lots_qty)
                if total >= needed_qty:
                    chosen = (dye_lot, lots_qty)
                    break

            if not chosen:
                suggestions.append(_(
                    '⚠ %s: không có Lot nhuộm nào đủ %.2f %s riêng lẻ — '
                    'cần phối màu thủ công, kiểm tra kỹ trước khi cắt.'
                ) % (move.product_id.display_name, needed_qty, move.product_uom.name))
                continue

            dye_lot, lots_qty = chosen
            remaining = needed_qty
            move_line_vals = []
            for lot, qty in sorted(lots_qty, key=lambda x: x[0].create_date):
                if remaining <= 0:
                    break
                take = min(qty, remaining)
                move_line_vals.append((0, 0, {
                'lot_id': lot.id,
                'product_id': move.product_id.id,
                'quantity': take,
                'product_uom_id': move.product_uom.id,
                'location_id': move.location_id.id,
                'location_dest_id': move.location_dest_id.id,
            }))
                remaining -= take

            # Xóa gợi ý cũ (nếu bấm lại nút nhiều lần) trước khi ghi gợi ý mới
            move.move_line_ids.filtered(lambda l: not l.picked).unlink()
            move.write({'move_line_ids': move_line_vals})

            suggestions.append(_(
                '✓ %s: gợi ý %s cây vải cùng Lot nhuộm "%s" (đủ %.2f %s).'
            ) % (move.product_id.display_name, len(move_line_vals), dye_lot,
                 needed_qty, move.product_uom.name))

        self.message_post(body='<br/>'.join(suggestions))
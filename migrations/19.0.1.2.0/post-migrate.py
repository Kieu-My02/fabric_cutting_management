# -*- coding: utf-8 -*-
"""Nhóm 1 - Trừ tồn theo QC Pass: script migration dữ liệu.

Trước bản 19.0.1.2.0, mọi cây vải (bất kể qc_state) đều nằm thẳng trong kho
chính (thường là WH/Stock) ngay sau khi nhập kho. Từ bản này, cây vải chưa
PASS phải nằm ở vị trí "Khu chờ QC" (xem data/fabric_qc_location_data.xml).

Script này chạy MỘT LẦN khi nâng cấp module lên bản 19.0.1.2.0 (post-migrate,
tức sau khi dữ liệu XML mới - bao gồm vị trí "Khu chờ QC" - đã được nạp), để
đưa các cây vải "Chờ kiểm"/"FAIL" đang tồn tại sẵn (dữ liệu cũ) về đúng vị trí
theo quy tắc nghiệp vụ mới, thay vì âm thầm để chúng tiếp tục nằm trong kho
chính và vẫn được Lệnh Cắt đặt chỗ như trước.

Cây vải đã PASS trước đó không bị đụng tới - chúng vốn đã "khả dụng" đúng
nghĩa, không cần chuyển đi đâu cả.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    quarantine = env.ref(
        'fabric_cutting_management.location_qc_quarantine', raise_if_not_found=False)
    if not quarantine:
        _logger.warning(
            'Nhóm 1 migration: không tìm thấy vị trí "Khu chờ QC" (data chưa nạp?), bỏ qua.')
        return

    lots_to_fix = env['stock.lot'].search([
        ('qc_state', 'in', ('pending', 'fail')),
    ])
    if not lots_to_fix:
        return

    moved_count = 0
    for lot in lots_to_fix:
        # Chỉ xử lý số lượng đang nằm ở vị trí nội bộ KHÁC "Khu chờ QC"
        # (ví dụ WH/Stock) - đây chính là phần tồn kho lẽ ra không nên được
        # tính là "khả dụng" theo quy tắc mới.
        quants = lot.quant_ids.filtered(
            lambda q: q.location_id.usage == 'internal'
            and q.location_id != quarantine
            and q.quantity > 0
        )
        if not quants:
            continue

        # Gom theo từng vị trí nguồn, mỗi vị trí một move riêng để giữ
        # nguyên thông tin truy vết (không gộp bừa nhiều vị trí vào 1 move).
        by_location = {}
        for quant in quants:
            by_location.setdefault(quant.location_id, 0.0)
            by_location[quant.location_id] += quant.quantity

        picking_type = lot._get_qc_release_picking_type()
        for src_location, qty in by_location.items():
            move = env['stock.move'].create({
                'description_picking': 'Migration Nhóm 1 - đưa về Khu chờ QC: %s' % lot.name,
                'product_id': lot.product_id.id,
                'product_uom_qty': qty,
                'product_uom': lot.product_id.uom_id.id,
                'location_id': src_location.id,
                'location_dest_id': quarantine.id,
                'picking_type_id': picking_type.id,
                'company_id': lot.company_id.id or env.company.id,
            })
            move._action_confirm()
            move._action_assign()
            if not move.move_line_ids:
                move.move_line_ids = [(0, 0, {
                    'product_id': lot.product_id.id,
                    'lot_id': lot.id,
                    'quantity': qty,
                    'product_uom_id': lot.product_id.uom_id.id,
                    'location_id': src_location.id,
                    'location_dest_id': quarantine.id,
                })]
            else:
                move.move_line_ids.write({'lot_id': lot.id, 'quantity': qty})
            move.move_line_ids.write({'picked': True})
            move._action_done()
            moved_count += 1

    _logger.info(
        'Nhóm 1 migration: đã chuyển %s dịch chuyển nội bộ đưa cây vải '
        'Chờ kiểm/FAIL hiện có về "Khu chờ QC".', moved_count)

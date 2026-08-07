# -*- coding: utf-8 -*-
"""Khắc phục lỗ hổng: stock.lot.fabric_supplier_id trước bản này là field
nhập tay thuần tuý, KHÔNG có onchange/default nào tự điền từ NCC của
Purchase Order khi nhận hàng. Hệ quả: fabric.supplier.scorecard.report
(Thẻ điểm NCC) join lot_agg theo sl.fabric_supplier_id - nếu field này
trống, tất cả cây vải "biến mất" khỏi thống kê PASS QC/FAIL QC của NCC
tương ứng dù qc_state trên chính lot đó đã là 'pass'/'fail' thật, khiến
Tỷ lệ PASS QC hiển thị 0.00% cho MỌI NCC bất kể QC thực tế thế nào.

Bản 19.0.1.12.0 thêm stock_picking.button_validate hook để tự điền field
này ngay khi phiếu nhập (receipt) được xác nhận - nhưng đó chỉ áp dụng cho
các phiếu nhập validate SAU khi nâng cấp. Script này chạy MỘT LẦN để backfill
lại cho các lot đã nhập/đã QC từ trước, dựa trên move nhập kho gốc (đầu tiên)
liên kết với move.purchase_line_id của lot đó."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    lots = env['stock.lot'].with_context(active_test=False).search([
        ('fabric_supplier_id', '=', False),
        ('product_id.product_tmpl_id.is_fabric', '=', True),
    ])
    if not lots:
        return

    updated = 0
    for lot in lots:
        move_line = env['stock.move.line'].search([
            ('lot_id', '=', lot.id),
            ('state', '=', 'done'),
            ('move_id.purchase_line_id', '!=', False),
        ], order='date asc', limit=1)
        if move_line:
            lot.fabric_supplier_id = move_line.move_id.purchase_line_id.order_id.partner_id.id
            updated += 1

    _logger.info(
        'Nhóm 10 migration (fabric_supplier_id backfill): đã cập nhật %s/%s '
        'stock.lot dựa trên move nhập kho gốc từ Purchase Order, để Thẻ điểm '
        'NCC tính đúng PASS QC/FAIL QC cho dữ liệu đã có từ trước.',
        updated, len(lots))

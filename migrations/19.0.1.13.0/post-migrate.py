# -*- coding: utf-8 -*-
"""Chuẩn hoá tên Lot/Serial về đúng format thống nhất: LOT-XXXXX-MMDDYYYY-NN.

Trước bản này, hàm sinh tên tự động (stock_lot_auto_name._generate_fabric_lot_name)
chỉ chạy khi field 'name' đang trống hoặc đang là số tự động thuần chữ số
(placeholder do Odoo sinh, ví dụ "0000011"). Các lot được TẠO TRƯỚC KHI module
này cài đặt, hoặc được NGƯỜI DÙNG NHẬP TAY tên lot (bỏ qua auto-name), vẫn giữ
tên theo các format cũ/không nhất quán, ví dụ:
  - LOT-DNDKB-290726-01     (ngày chỉ 6 số, sai thứ tự DDMMYY thay vì MMDDYYYY)
  - LOT-CTWHT-260726        (thiếu số thứ tự NN)
  - LOT-DNDKB-02            (không có ngày)
  - FAB-BE-KKI-20260724-001 (prefix khác, số thứ tự 3 chữ số)
  - FAB-KK-BEI              (không có ngày, không có số thứ tự)

Hệ quả: cột "Lot/Serial Number" hiển thị độ dài không đều, khó tra cứu/sort,
và các báo cáo/đối chiếu dựa theo pattern tên lot (nếu có) sẽ bỏ sót các lot này.

Script này chạy MỘT LẦN, quét toàn bộ stock.lot theo thứ tự create_date, nhóm
theo ngày tạo thực tế của từng lot. Với các lot đã đúng chuẩn, GIỮ NGUYÊN tên
và số thứ tự (NN) hiện có để không phá vỡ tham chiếu đang tồn tại (chứng từ,
báo cáo, mã vạch đã in...). Với các lot sai chuẩn, sinh tên mới theo đúng
format, tự động chọn số thứ tự (NN) tiếp theo còn trống trong ngày đó để
không trùng với các lot đã đúng chuẩn.
"""
import logging
import re
from collections import defaultdict

from odoo import api, SUPERUSER_ID, fields

_logger = logging.getLogger(__name__)

LOT_NAME_PATTERN = re.compile(r'^LOT-[A-Z0-9]{5}-\d{8}-\d{2}$')


def _clean_suffix(product):
    code = (product.default_code or product.name or 'XXXXX')
    clean_code = re.sub(r'[^A-Za-z0-9]', '', code).upper()
    return clean_code[-5:] if len(clean_code) >= 5 else clean_code.rjust(5, 'X')


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Lot = env['stock.lot']

    lots = Lot.with_context(active_test=False).search([], order='create_date asc')
    if not lots:
        return

    # Nhóm các lot theo NGÀY TẠO THỰC TẾ (theo timezone công ty)
    by_day = defaultdict(list)
    skipped_no_date = 0
    for lot in lots:
        if not lot.create_date:
            skipped_no_date += 1
            continue
        local_dt = fields.Datetime.context_timestamp(lot, lot.create_date)
        by_day[local_dt.date()].append(lot)

    renamed = 0
    skipped_no_product = 0
    renamed_log = []

    for day, day_lots in by_day.items():
        # Số thứ tự (NN) đã bị các lot ĐÚNG CHUẨN chiếm giữ trong ngày -> giữ nguyên, không đụng vào
        used_seq = set()
        for lot in day_lots:
            if lot.name and LOT_NAME_PATTERN.match(lot.name):
                used_seq.add(lot.name.rsplit('-', 1)[-1])

        next_seq = 1
        for lot in day_lots:
            if lot.name and LOT_NAME_PATTERN.match(lot.name):
                continue  # đã đúng chuẩn, không đổi

            if not lot.product_id:
                skipped_no_product += 1
                continue

            while f'{next_seq:02d}' in used_seq:
                next_seq += 1
            seq_str = f'{next_seq:02d}'
            used_seq.add(seq_str)
            next_seq += 1

            suffix = _clean_suffix(lot.product_id)
            date_str = day.strftime('%m%d%Y')
            new_name = f'LOT-{suffix}-{date_str}-{seq_str}'

            old_name = lot.name
            lot.name = new_name
            renamed += 1
            renamed_log.append((old_name, new_name, lot.product_id.display_name))

    for old_name, new_name, product_name in renamed_log:
        _logger.info('Đổi tên lot: "%s" -> "%s" (sản phẩm: %s)', old_name, new_name, product_name)

    _logger.info(
        'Migration chuẩn hoá tên Lot (19.0.1.13.0): đã đổi tên %s/%s lot về đúng chuẩn '
        'LOT-XXXXX-MMDDYYYY-NN. Bỏ qua %s lot không có product_id, %s lot không có create_date.',
        renamed, len(lots), skipped_no_product, skipped_no_date)

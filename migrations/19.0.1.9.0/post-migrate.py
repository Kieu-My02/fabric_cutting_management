# -*- coding: utf-8 -*-
"""Khắc phục lỗ hổng thiết kế: trước bản này, toàn bộ module (điều hướng QC,
gợi ý cây vải, và 3 Báo cáo Chênh lệch định mức/Dự báo thiếu vải/Insight) đều
NGẦM ĐỊNH coi "default_fabric_gsm > 0" là tiêu chí để nhận diện một sản phẩm
CÓ PHẢI LÀ VẢI hay không. Hệ quả:

1) Tab "Định mức Vải (FR-07)" hiện ra với đủ 6 trường cho MỌI sản phẩm, kể cả
   phụ liệu may (chỉ, khuy, nhãn...) không liên quan - gây rối mắt, dễ nhập
   nhầm.
2) Nghiêm trọng hơn: nếu một mã THẬT SỰ LÀ VẢI nhưng nhân viên quên điền GSM
   (để mặc định 0.00), sản phẩm đó sẽ ÂM THẦM biến mất khỏi toàn bộ Báo cáo
   Chênh lệch định mức/Dự báo thiếu vải/Insight mà không có bất kỳ cảnh báo
   nào - rủi ro dữ liệu thật sự, không chỉ là vấn đề giao diện.

Bản này tách hẳn "là vải hay không" thành 1 cờ tường minh, độc lập:
product.template.is_fabric (mặc định False, do người dùng khai báo tay) -
và thay toàn bộ điều kiện default_fabric_gsm > 0 trong code/SQL report bằng
is_fabric = True.

Script migration này chạy MỘT LẦN khi nâng cấp lên bản 19.0.1.9.0, để các mã
đã được cấu hình là vải TỪ TRƯỚC (default_fabric_gsm > 0) tiếp tục được nhận
diện đúng như cũ, thay vì mặc định is_fabric=False khiến chúng đột ngột biến
mất khỏi mọi báo cáo/luồng nghiệp vụ ngay sau khi nâng cấp.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    templates = env['product.template'].with_context(active_test=False).search([
        ('default_fabric_gsm', '>', 0),
    ])
    if not templates:
        return

    templates.write({'is_fabric': True})
    _logger.info(
        'Nhóm 6 migration (is_fabric): đã đánh dấu %s product.template có sẵn '
        'Định lượng chuẩn (GSM) > 0 là "Là mã Vải" để giữ nguyên hành vi báo '
        'cáo/nghiệp vụ trước khi nâng cấp.', len(templates))

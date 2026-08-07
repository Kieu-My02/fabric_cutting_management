# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime
import re

# Chuẩn bắt buộc: LOT-[5 ký tự mã SP]-MMDDYYYY-[2 số thứ tự]
LOT_NAME_PATTERN = re.compile(r'^LOT-[A-Z0-9]{5}-\d{8}-\d{2}$')


class StockLotAutoName(models.Model):
    _inherit = 'stock.lot'

    def _generate_fabric_lot_name(self, product_id, ref_date=None):
        """Sinh tên Lot theo chuẩn: LOT-[5 ký tự cuối mã SP]-MMDDYYYY-NN"""
        ref_date = ref_date or fields.Date.context_today(self)
        if isinstance(ref_date, str):
            ref_date = fields.Date.from_string(ref_date)

        date_str = ref_date.strftime('%m%d%Y')

        product = self.env['product.product'].browse(product_id)
        code = (product.default_code or product.name or 'XXXXX')
        # Lấy 5 ký tự cuối, bỏ khoảng trắng/dấu gạch dư thừa, viết hoa
        clean_code = re.sub(r'[^A-Za-z0-9]', '', code).upper()
        suffix = clean_code[-5:] if len(clean_code) >= 5 else clean_code.rjust(5, 'X')

        # Đếm số lot đã tạo trong cùng ngày (không phân biệt sản phẩm)
        day_start = datetime.combine(ref_date, datetime.min.time())
        day_end = datetime.combine(ref_date, datetime.max.time())
        count_today = self.search_count([
            ('create_date', '>=', fields.Datetime.to_string(day_start)),
            ('create_date', '<=', fields.Datetime.to_string(day_end)),
        ])
        seq = count_today + 1

        return f"LOT-{suffix}-{date_str}-{seq:02d}"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Chỉ tự sinh tên khi: name trống, hoặc Odoo đang định dùng số tự động
            # (số tự động thường thuần chữ số, VD "0000011")
            current_name = vals.get('name')
            is_auto_placeholder = (
                not current_name
                or (isinstance(current_name, str) and current_name.isdigit())
            )
            product_id = vals.get('product_id')
            if is_auto_placeholder and product_id:
                vals['name'] = self._generate_fabric_lot_name(product_id)
        return super().create(vals_list)

    @api.constrains('name')
    def _check_fabric_lot_name_format(self):
        """Chặn nhập tay tên lot sai chuẩn LOT-XXXXX-MMDDYYYY-NN.

        Chỉ áp dụng cho lot có product_id (đúng phạm vi mà
        _generate_fabric_lot_name/create() ở trên xử lý), để không ảnh hưởng
        tới lot của các module/app khác không thuộc nghiệp vụ này.
        """
        for lot in self:
            if not lot.product_id or not lot.name:
                continue
            if not LOT_NAME_PATTERN.match(lot.name):
                raise ValidationError(_(
                    'Tên Lot "%s" không đúng chuẩn.\n'
                    'Yêu cầu: LOT-XXXXX-MMDDYYYY-NN\n'
                    '  - XXXXX: 5 ký tự cuối mã sản phẩm (viết hoa, chỉ chữ/số)\n'
                    '  - MMDDYYYY: ngày tạo (tháng-ngày-năm, đủ 8 số)\n'
                    '  - NN: số thứ tự trong ngày (2 chữ số)\n'
                    'Ví dụ: LOT-CTWHT-07312026-06\n\n'
                    'Hãy để trống tên Lot để hệ thống tự sinh, hoặc nhập đúng format trên.'
                ) % lot.name)

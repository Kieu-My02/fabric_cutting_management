# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    """FR-07: định lượng (GSM) và khổ vải chuẩn theo từng mã hàng, dùng để
    gợi ý mặc định lên stock.lot khi tạo cây vải mới, giảm sai sót gõ tay."""

    _inherit = 'product.template'

    is_fabric = fields.Boolean(
        string='Là mã Vải',
        help='Đánh dấu sản phẩm này LÀ vải (khác với phụ liệu may như chỉ, khuy, '
             'nhãn...). Đây là tiêu chí DUY NHẤT được toàn bộ module dùng để nhận '
             'diện "vải" (điều hướng QC, gợi ý cây vải, các Báo cáo Chênh lệch định '
             'mức/Dự báo thiếu vải/Insight...). Chỉ khi bật cờ này các trường Định '
             'mức Vải (FR-07) bên dưới mới hiển thị để nhập liệu - không còn suy '
             'luận ngầm qua Định lượng chuẩn (GSM) > 0 như trước, vì cách cũ khiến '
             'một mã vải thật sự nhưng lỡ bỏ trống GSM sẽ âm thầm biến mất khỏi các '
             'báo cáo mà không có cảnh báo nào.',
    )
    default_fabric_gsm = fields.Float(
        string='Định lượng chuẩn (GSM)',
        help='Định lượng chuẩn (g/m²) của mã vải này, dùng để gợi ý mặc định '
             'khi tạo cây vải (Lot) mới. Không ảnh hưởng các lô đã tạo trước đó.',
    )
    default_fabric_width = fields.Float(
        string='Khổ vải chuẩn (cm)',
        help='Khổ vải chuẩn (cm) của mã vải này, dùng để gợi ý mặc định '
             'khi tạo cây vải (Lot) mới.',
    )
    default_fabric_waste_percent = fields.Float(
        string='Tỷ lệ hao hụt cho phép (%)',
        help='FR-02: tỷ lệ % vượt định mức tối đa mà Phòng Sample cho phép khi đặt mua '
             'vải này. Dùng để cảnh báo Thu mua khi số lượng đặt vượt ngưỡng so với nhu '
             'cầu Lệnh Cắt (Cut Ticket) liên kết trên dòng đặt hàng.',
    )
    fabric_norm_variance_threshold = fields.Float(
        string='Ngưỡng cảnh báo chênh lệch định mức cắt (%)',
        help='FR-12: % chênh lệch tối đa cho phép giữa định mức lý thuyết (nhu cầu theo '
             'BoM của Lệnh Cắt) và số lượng thực tế đã tiêu thụ (move đã "picked") trước '
             'khi Báo cáo chênh lệch định mức đánh dấu cảnh báo. Để trống sẽ dùng ngưỡng '
             'mặc định 5%.',
    )
    fabric_lead_time_days = fields.Float(
        string='Thời gian giao hàng NCC (ngày)',
        help='FR-13: số ngày trung bình từ lúc đặt hàng đến lúc nhận được vải này từ '
             'Nhà cung cấp, dùng để tính Điểm đặt hàng lại (Reorder Point) trên Báo cáo '
             'Dự báo thiếu vải. Để trống sẽ dùng mặc định 15 ngày.',
    )
    fabric_safety_stock_days = fields.Float(
        string='Số ngày tồn an toàn (Safety Stock)',
        help='FR-13: số ngày tiêu thụ mà kho luôn muốn có sẵn dự phòng thêm ngoài thời '
             'gian giao hàng của NCC, dùng để tính Điểm đặt hàng lại trên Báo cáo Dự báo '
             'thiếu vải. Để trống sẽ dùng mặc định 7 ngày.',
    )

    @api.constrains('is_fabric', 'uom_id', 'uom_po_id')
    def _check_fabric_uom_is_length(self):
        """Toàn bộ module giả định vải được đo theo đơn vị CHIỀU DÀI (yard là
        mặc định của FR-14 fabric.return, xem fabric_return.py) - các Báo cáo
        Chênh lệch định mức/Insight/Thẻ điểm NCC (FR-12/15/16) đều cộng dồn số
        lượng vải giữa nhiều Lệnh Cắt/NCC với NHAU, giả định tất cả cùng 1 loại
        đơn vị đo. Nếu 1 mã Vải lỡ để UOM = Units (đơn vị đếm rời, dùng cho phụ
        liệu như khuy, nhãn), số liệu tổng hợp sẽ cộng lẫn 2 loại đơn vị khác
        nhau (vd: yard + units) mà không có cảnh báo gì - lỗi rất khó phát hiện
        vì Odoo vẫn cho lưu bình thường (không sai kiểu dữ liệu, chỉ sai đơn vị
        đo cho mã vải). Constraint này chặn việc lưu SAI ngay tại nguồn.

        Chỉ áp dụng cho product.template có is_fabric=True; các mã phụ liệu
        (chỉ, khuy, nhãn...) không bị ảnh hưởng."""
        length_category = self.env.ref('uom.uom_categ_length', raise_if_not_found=False)
        if not length_category:
            # Category Length không tồn tại trên DB này (hiếm khi xảy ra, chỉ
            # phòng hờ nếu dữ liệu chuẩn uom bị xoá/đổi tên) - bỏ qua thay vì
            # chặn cứng, để không khoá form sản phẩm vì lý do ngoài ý muốn.
            return
        for template in self:
            if not template.is_fabric:
                continue
            uoms_to_check = template.uom_id | template.uom_po_id
            wrong_uoms = uoms_to_check.filtered(
                lambda u: u.category_id != length_category)
            if wrong_uoms:
                raise ValidationError(
                    "Mã Vải '%s' đang đặt Đơn vị tính là %s - không thuộc nhóm "
                    "Chiều dài (Length/Distance).\n\n"
                    "Toàn bộ module Cấp phát Vải (Chênh lệch định mức, Insights, "
                    "Thẻ điểm NCC...) giả định các mã Vải đo theo đơn vị chiều "
                    "dài (yard, mét, cm...) để có thể cộng dồn/so sánh số lượng "
                    "giữa nhiều Lệnh Cắt và Nhà cung cấp với nhau. Vui lòng đổi "
                    "Đơn vị tính (và Đơn vị mua hàng nếu có) của mã này sang "
                    "Yard/Mét/Cm - hoặc bỏ đánh dấu 'Là mã Vải' nếu đây thực sự "
                    "là phụ liệu đếm theo cái (khuy, nhãn, chỉ...)." % (
                        template.name, ', '.join(wrong_uoms.mapped('name')))
                )
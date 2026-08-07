# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    """Hỗ trợ nút thống kê 'Đổi trả/Lãnh bù' trên form Purchase Order,
    liên kết tới các fabric.return.request phát sinh từ đơn mua này.

    Nhóm 1 - Trừ tồn theo QC Pass: sau khi PO được xác nhận (button_confirm
    đã tạo xong phiếu nhập), các move nguyên liệu VẢI (nhận diện như FR-08:
    tracking theo Lot và có cờ is_fabric) được điều hướng
    đích đến là "Khu chờ QC" thay vì thẳng vào kho chính. Chỉ khi QC đánh
    dấu PASS (stock_lot.action_set_qc_pass) hệ thống mới tự tạo dịch chuyển
    nội bộ đưa vào kho chính - lúc đó mới thật sự khả dụng cho Lệnh Cắt.

    Nhận diện "vải" dùng cờ product.template.is_fabric (khai báo tay), không
    còn dùng default_fabric_gsm > 0 làm proxy - tránh trường hợp một mã vải
    thật sự nhưng bị bỏ trống GSM lại không được điều hướng qua QC."""

    _inherit = 'purchase.order'

    def button_confirm(self):
        result = super().button_confirm()
        quarantine = self.env.ref(
            'fabric_cutting_management.location_qc_quarantine', raise_if_not_found=False)
        if quarantine:
            for order in self:
                fabric_moves = order.picking_ids.move_ids.filtered(
                    lambda m: m.state not in ('done', 'cancel')
                    and m.location_dest_id.usage == 'internal'
                    and m.location_dest_id != quarantine
                    and m.product_id.tracking == 'lot'
                    and m.product_id.product_tmpl_id.is_fabric
                )
                if fabric_moves:
                    fabric_moves.write({'location_dest_id': quarantine.id})
                    fabric_moves.move_line_ids.write({'location_dest_id': quarantine.id})

        # Nhóm 8 - Cọc mua vải: "đóng băng" (freeze) số tiền cọc phải trả
        # NGAY TẠI THỜI ĐIỂM xác nhận PO, dựa trên deposit_percent và
        # amount_total hiện tại. Cố tình KHÔNG dùng field compute thông
        # thường cho deposit_amount vì amount_total của PO có thể còn thay
        # đổi về sau (sửa số lượng/giá dòng đặt hàng sau khi đã confirm,
        # phụ phí phát sinh...) - nếu để compute tự động, số tiền cọc sẽ
        # trôi theo mỗi lần amount_total đổi, không còn đúng nghĩa "số tiền
        # NCC đã yêu cầu đặt cọc tại thời điểm chốt đơn" nữa. Chỉ tính lại
        # một lần duy nhất ở đây; nếu order đã có deposit_amount từ trước
        # (ví dụ bị Huỷ rồi Xác nhận lại) thì giữ nguyên, không ghi đè.
        for order in self:
            if order.deposit_percent and not order.deposit_amount:
                order.deposit_amount = order.amount_total * order.deposit_percent / 100.0
            # Tự động sinh hoá đơn cọc (NHÁP) ngay khi vừa chốt deposit_amount
            # ở trên - nhưng chỉ tạo một lần duy nhất cho mỗi đơn (nếu order
            # đã có deposit_invoice_id từ trước, ví dụ do bị Huỷ rồi Xác nhận
            # lại, thì KHÔNG tạo hoá đơn cọc thứ hai).
            if order.deposit_amount and not order.deposit_invoice_id:
                order.deposit_invoice_id = order._create_deposit_invoice_draft()
        return result

    def _get_deposit_invoice_account(self):
        """Xác định tài khoản (account.account) dùng cho dòng hoá đơn cọc.
        Ưu tiên tài khoản chi phí mua hàng (account 'expense') của mặt hàng
        đầu tiên trên đơn theo Danh mục sản phẩm - đây là cách xác định tài
        khoản tự nhiên nhất vì hoá đơn cọc thực chất là ứng trước cho cùng
        loại hàng hoá đó. Nếu vì lý do nào đó không xác định được (thiếu cấu
        hình kế toán trên Danh mục sản phẩm), fallback về tài khoản Phải trả
        mặc định của NCC (property_account_payable_id) để việc tạo hoá đơn
        cọc không bị chặn đứng chỉ vì thiếu một cấu hình chi tiết."""
        self.ensure_one()
        account = self.env['account.account']
        first_product = self.order_line[:1].product_id
        if first_product:
            account = first_product._get_product_accounts().get('expense')
        if not account:
            account = self.partner_id.property_account_payable_id
        return account

    def _create_deposit_invoice_draft(self):
        """Nhóm 8 - Cọc mua vải: tự động sinh hoá đơn cọc (vendor bill) ở
        trạng thái NHÁP (draft) ngay khi PO được xác nhận, dựa trên
        deposit_amount đã "đóng băng" ở button_confirm. CỐ Ý dừng lại ở
        draft, KHÔNG tự động post - việc vào sổ (post) hoá đơn vẫn phải do
        Kế toán rà soát & duyệt thủ công, đúng quy trình thực tế (Thu mua
        xác nhận đơn không đồng nghĩa Kế toán đã đồng ý ghi nhận công nợ
        ngay). Hoá đơn cọc này KHÔNG gắn account.move.line.purchase_line_id
        tới order_line của PO - cố tình tách biệt khỏi purchase.order.
        invoice_ids/amount_billed (công nợ hàng hoá chính thức, xem
        _compute_payment_state) để tránh nhầm lẫn giữa công nợ cọc và công
        nợ hàng hoá chính thức của cùng một đơn mua."""
        self.ensure_one()
        account = self._get_deposit_invoice_account()
        if not account:
            return False
        move = self.env['account.move'].sudo().create({
            'move_type': 'in_invoice',
            'partner_id': self.partner_id.id,
            'invoice_origin': self.name,
            'currency_id': self.currency_id.id,
            # Bắt buộc phải có Invoice Date thì mới Post được (Odoo yêu cầu
            # "Bill/Refund date is required"). Mặc định lấy ngày hiện tại -
            # Kế toán vẫn có thể sửa lại tay trước khi Post nếu cần đúng
            # ngày hoá đơn thực tế của NCC.
            'invoice_date': fields.Date.context_today(self),
            'ref': _('Cọc mua vải - %s') % self.name,
            'invoice_line_ids': [(0, 0, {
                'name': _('Tiền cọc mua vải theo đơn %(order)s (%(percent).2g%%)') % {
                    'order': self.name,
                    'percent': self.deposit_percent,
                },
                'account_id': account.id,
                'quantity': 1,
                'price_unit': self.deposit_amount,
            })],
        })
        return move.id

    @api.onchange('partner_id')
    def _onchange_partner_id_fabric_deposit(self):
        """Gợi ý % cọc mặc định theo NCC (res.partner.fabric_deposit_percent)
        khi vừa chọn NCC trên đơn mua còn ở trạng thái Nháp/Đang gửi báo giá.
        Chỉ điền khi deposit_percent đang trống - không ghi đè giá trị Thu
        mua đã tự sửa tay cho đơn hiện tại."""
        for order in self:
            if order.partner_id and not order.deposit_percent:
                order.deposit_percent = order.partner_id.fabric_deposit_percent

    fabric_return_count = fields.Integer(
        string='Số yêu cầu Đổi trả/Lãnh bù', compute='_compute_fabric_return_count',
    )

    # Nhóm 8 - Cọc mua vải: % cọc áp dụng riêng cho đơn mua này. Mặc định
    # gợi ý từ NCC (xem _onchange_partner_id_fabric_deposit) nhưng Thu mua
    # có thể sửa tay cho từng đơn cụ thể (ví dụ đơn gấp/khách quen thương
    # lượng tỷ lệ khác) - không hardcode một tỷ lệ chung 30% cho mọi NCC/PO.
    deposit_percent = fields.Float(
        string='% Cọc', copy=False,
        help='Tỷ lệ %% cọc áp dụng cho đơn mua này. Mặc định lấy từ chính '
             'sách của NCC (res.partner.fabric_deposit_percent) khi chọn NCC, '
             'nhưng có thể sửa tay riêng cho từng đơn trước khi xác nhận.',
    )
    # Số tiền cọc THỰC TẾ, được "đóng băng" một lần duy nhất tại thời điểm
    # button_confirm (xem ghi chú trong button_confirm ở trên) - không phải
    # field compute nên sẽ KHÔNG tự đổi theo nếu amount_total thay đổi sau
    # khi đơn đã được xác nhận.
    deposit_amount = fields.Monetary(
        string='Số tiền cọc (đã chốt)', copy=False, currency_field='currency_id',
        help='Số tiền cọc phải trả NCC, được tính = amount_total x deposit_percent '
             'NGAY TẠI THỜI ĐIỂM xác nhận đơn mua và giữ cố định từ đó về sau, kể '
             'cả khi amount_total của đơn thay đổi sau này. Để trống nếu đơn '
             'chưa xác nhận hoặc không yêu cầu đặt cọc (deposit_percent = 0).',
    )
    deposit_invoice_id = fields.Many2one(
        'account.move', string='Hoá đơn cọc', copy=False,
        domain=[('move_type', '=', 'in_invoice')],
        help='Hoá đơn NCC (vendor bill) tương ứng với khoản tiền cọc deposit_amount, '
             'được TỰ ĐỘNG tạo ở trạng thái NHÁP ngay khi button_confirm (xem '
             '_create_deposit_invoice_draft) - Kế toán vẫn cần tự vào xem và Post/'
             'thanh toán thủ công, dùng để đối chiếu công nợ cọc riêng biệt với công '
             'nợ hàng hoá chính thức.',
    )
    # Nhóm 8 - điều kiện cho nút "Thanh toán phần còn lại cho NCC" (xem
    # action_pay_supplier_vnpay + view): TRUE khi đơn KHÔNG yêu cầu cọc
    # (deposit_amount = 0, không có gì phải chờ) HOẶC hoá đơn cọc đã ở
    # trạng thái Đã thanh toán/Đang xử lý thanh toán. Tách thành field riêng
    # (thay vì viết trực tiếp field quan hệ deposit_invoice_id.payment_state
    # trong domain/invisible của view) để tránh phụ thuộc vào cách view
    # engine xử lý truy vấn qua dấu chấm trên field quan hệ.
    deposit_invoice_paid = fields.Boolean(
        string='Đã xử lý xong cọc', compute='_compute_deposit_invoice_paid',
        help='TRUE khi đơn không yêu cầu cọc, hoặc hoá đơn cọc đã Đã thanh toán/'
             'Đang xử lý thanh toán. Dùng làm điều kiện hiển thị nút "Thanh toán '
             'phần còn lại cho NCC" - chỉ nên trả phần còn lại sau khi khoản cọc '
             'đã được xử lý xong, tránh nhầm lẫn thứ tự thanh toán với NCC.',
    )

    @api.depends('deposit_amount', 'deposit_invoice_id.payment_state')
    def _compute_deposit_invoice_paid(self):
        for order in self:
            if not order.deposit_amount:
                order.deposit_invoice_paid = True
            else:
                order.deposit_invoice_paid = bool(
                    order.deposit_invoice_id
                    and order.deposit_invoice_id.payment_state in ('paid', 'in_payment')
                )

    # Nhóm 8 - Trừ tồn theo QC Pass (mở rộng): trạng thái QC PASS được xét
    # trên TOÀN BỘ các cây vải (stock.lot) đã nhận về từ PO này, KHÔNG phải
    # xét từng lot riêng lẻ - PO chỉ được xem là "đã qua QC" khi TẤT CẢ các
    # cây vải liên quan đều PASS. Nếu còn dù chỉ một cây đang "Chờ kiểm"
    # hoặc bị "FAIL", cả PO vẫn coi là CHƯA đạt (False), để tránh nhầm lẫn
    # khi chỉ xem trạng thái của một lot mà tưởng cả đơn đã sẵn sàng cấp
    # phát cho Lệnh Cắt. PO chưa nhận cây vải nào (fabric_lots rỗng) cũng
    # coi là CHƯA đạt, vì thực chất chưa có gì để cấp phát.
    is_qc_fully_passed = fields.Boolean(
        string='Đã PASS QC toàn bộ', compute='_compute_is_qc_fully_passed', store=True,
        help='TRUE khi TẤT CẢ cây vải (stock.lot) đã nhận từ PO này đều có '
             'Trạng thái QC = PASS. Nếu còn cây nào đang Chờ kiểm/FAIL, hoặc '
             'PO chưa nhận cây vải nào, giá trị là FALSE.',
    )

    @api.constrains('deposit_percent')
    def _check_deposit_percent(self):
        for order in self:
            if not (0.0 <= order.deposit_percent <= 100.0):
                raise UserError(_(
                    '%% Cọc của đơn mua %s phải trong khoảng 0-100%%.'
                ) % order.name)

    @api.depends(
        'picking_ids.move_ids.state',
        'picking_ids.move_ids.product_id.product_tmpl_id.is_fabric',
        'picking_ids.move_ids.move_line_ids.lot_id.qc_state',
    )
    def _compute_is_qc_fully_passed(self):
        for order in self:
            fabric_moves = order.picking_ids.move_ids.filtered(
                lambda m: m.state == 'done'
                and m.product_id.tracking == 'lot'
                and m.product_id.product_tmpl_id.is_fabric
            )
            fabric_lots = fabric_moves.move_line_ids.lot_id
            order.is_qc_fully_passed = bool(fabric_lots) and all(
                lot.qc_state == 'pass' for lot in fabric_lots
            )

    # Nhóm 7: tổng hợp tình trạng thanh toán (công nợ phải trả) từ hoá đơn
    # mua hàng ĐÃ VÀO SỔ (posted) sinh ra từ PO này - đối xứng với
    # payment_state phía Đơn hàng bán (xem sale_order.py, Nhóm 5-6). Cùng
    # tinh thần đơn giản hoá cho mục đích dashboard/cảnh báo của đồ án -
    # account.move vẫn là nguồn dữ liệu gốc, đáng tin cậy duy nhất. Không
    # store=True vì purchase.order.invoice_ids là field compute không lưu
    # trữ của Odoo lõi (dựa trên account.move.line.purchase_line_id) - không
    # có đường dẫn quan hệ ổn định để depends() theo dõi thay đổi ngược từ
    # account.move, nên để tính lại mỗi lần đọc thay vì lưu sai lệch dữ liệu.
    payment_state = fields.Selection(
        [
            ('not_billed', 'Chưa nhận hoá đơn'),
            ('not_paid', 'Chưa thanh toán'),
            ('in_payment', 'Đang xử lý thanh toán'),
            ('partial', 'Thanh toán một phần'),
            ('paid', 'Đã thanh toán'),
            ('reversed', 'Đã bị đảo (Reversed)'),
        ],
        string='Tình trạng thanh toán', compute='_compute_payment_state')
    amount_billed = fields.Monetary(
        string='Đã nhận hoá đơn', compute='_compute_payment_state', currency_field='currency_id')
    amount_residual = fields.Monetary(
        string='Còn phải trả NCC', compute='_compute_payment_state', currency_field='currency_id')

    @api.depends(
        'invoice_ids.payment_state', 'invoice_ids.amount_residual',
        'invoice_ids.amount_total', 'invoice_ids.state', 'invoice_ids.move_type',
    )
    def _compute_payment_state(self):
        for order in self:
            bills = order.invoice_ids.filtered(
                lambda m: m.move_type == 'in_invoice' and m.state == 'posted')
            if not bills:
                order.payment_state = 'not_billed'
                order.amount_billed = 0.0
                order.amount_residual = 0.0
                continue

            order.amount_billed = sum(bills.mapped('amount_total'))
            order.amount_residual = sum(bills.mapped('amount_residual'))
            states = set(bills.mapped('payment_state'))

            # Cùng thứ tự ưu tiên với sale.order._compute_payment_state - xem
            # giải thích chi tiết ở đó, áp dụng tương tự cho chiều phải trả.
            if 'partial' in states or (
                    0 < order.amount_residual < order.amount_billed):
                order.payment_state = 'partial'
            elif states == {'paid'}:
                order.payment_state = 'paid'
            elif states == {'reversed'}:
                order.payment_state = 'reversed'
            elif 'in_payment' in states:
                order.payment_state = 'in_payment'
            else:
                order.payment_state = 'not_paid'

    # Nhóm 7/8 (bổ sung) - nút thống kê "Lịch sử thanh toán VNPay": gộp cả
    # giao dịch của hoá đơn cọc (deposit_invoice_id, CỐ TÌNH không nằm trong
    # invoice_ids - xem ghi chú ở _create_deposit_invoice_draft) lẫn giao
    # dịch của các hoá đơn NCC chính thức (invoice_ids), để Thu mua thấy
    # được TOÀN BỘ các lần đã bấm "Thanh toán cọc" / "Thanh toán phần còn
    # lại cho NCC" của đúng đơn mua này (kể cả các giao dịch pending/lỗi/
    # huỷ, không chỉ giao dịch đã done) thay vì phải vào Kỹ thuật > Giao
    # dịch thanh toán để tự lọc tay theo hoá đơn.
    vnpay_transaction_count = fields.Integer(
        string='Số giao dịch VNPay', compute='_compute_vnpay_transaction_count')

    @api.depends('invoice_ids', 'deposit_invoice_id')
    def _compute_vnpay_transaction_count(self):
        Tx = self.env['payment.transaction']
        for order in self:
            invoices = order.invoice_ids | order.deposit_invoice_id
            order.vnpay_transaction_count = Tx.search_count(
                [('invoice_ids', 'in', invoices.ids)]
            ) if invoices else 0

    def action_view_vnpay_transactions(self):
        self.ensure_one()
        invoices = self.invoice_ids | self.deposit_invoice_id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lịch sử thanh toán VNPay - %s') % self.name,
            'res_model': 'payment.transaction',
            'view_mode': 'list,form',
            'domain': [('invoice_ids', 'in', invoices.ids)],
            'context': {'create': False},
        }

    @api.depends('name')
    def _compute_fabric_return_count(self):
        FabricReturn = self.env['fabric.return.request']
        for order in self:
            order.fabric_return_count = FabricReturn.search_count([
                ('purchase_order_id', '=', order.id),
            ])

    def action_view_fabric_returns(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'fabric_cutting_management.action_fabric_return_request'
        )
        action['domain'] = [('purchase_order_id', '=', self.id)]
        action['context'] = {'default_purchase_order_id': self.id}
        return action

    # Nhóm 8 (bổ sung) - nút "Thanh toán cọc" ngay trong tab Cọc mua vải:
    # cho phép Thu mua bấm thanh toán khoản cọc NGAY khi vừa xác nhận đơn,
    # không cần rời sang phân hệ Kế toán để tự tìm & Post hoá đơn cọc trước.
    # Để đúng 1 lần bấm là xong, action này TỰ Post hoá đơn cọc nếu đang ở
    # Nháp (khác với hoá đơn hàng hoá chính thức - nơi việc Post vẫn cố tình
    # để Kế toán tự duyệt thủ công) - vì hoá đơn cọc bản chất là do chính
    # Thu mua/NCC yêu cầu tại thời điểm chốt đơn, không cần một vòng duyệt
    # riêng trước khi trả.
    def action_pay_deposit_vnpay(self):
        self.ensure_one()
        if not self.deposit_invoice_id:
            raise UserError(_(
                'Đơn mua %s chưa có hoá đơn cọc để thanh toán.'
            ) % self.name)
        if self.deposit_invoice_paid:
            raise UserError(_(
                'Khoản cọc của đơn mua %s đã được xử lý xong (đã thanh toán).'
            ) % self.name)

        # deposit_invoice_paid ở trên dựa vào deposit_invoice_id.payment_state,
        # field này CHỈ đổi sau khi Odoo core tạo account.payment và reconcile
        # với hoá đơn cọc (qua polling JS trên trang /payment/status hoặc cron
        # _cron_post_process) - việc này không đồng bộ, có độ trễ. Nếu chỉ
        # dựa vào deposit_invoice_paid, người dùng bấm nút này 2 lần liên tiếp
        # (hoặc quay lại bấm tiếp trong lúc giao dịch trước còn đang chờ
        # reconcile) sẽ tạo thêm payment.transaction thứ hai cho CÙNG một
        # hoá đơn cọc -> cọc bị trả 2 lần. Chặn thêm ở đây dựa trực tiếp vào
        # trạng thái payment.transaction, vì đó mới là nguồn gần nhất với
        # việc "VNPay đã xác nhận", không phụ thuộc độ trễ reconcile kế toán.
        pending_tx = self.env['payment.transaction'].sudo().search([
            ('invoice_ids', 'in', self.deposit_invoice_id.ids),
            ('state', 'in', ('pending', 'authorized', 'done')),
        ], limit=1)
        if pending_tx:
            raise UserError(_(
                'Đã có giao dịch thanh toán cọc (%(ref)s) đang xử lý hoặc đã '
                'thành công cho đơn %(order)s. Vui lòng đợi xác nhận trước khi '
                'tạo giao dịch mới.'
            ) % {'ref': pending_tx.reference, 'order': self.name})

        if self.deposit_invoice_id.state == 'draft':
            # Phòng trường hợp hoá đơn cọc này đã được tạo TRƯỚC KHI có fix
            # tự điền invoice_date lúc khởi tạo (_create_deposit_invoice_draft)
            # - luôn kiểm tra & bổ sung ngay tại đây trước khi Post, để không
            # phụ thuộc vào thời điểm hoá đơn được tạo ra.
            if not self.deposit_invoice_id.invoice_date:
                self.deposit_invoice_id.invoice_date = fields.Date.context_today(self)
            self.deposit_invoice_id.action_post()

        provider = self.env['payment.provider'].sudo().search(
            [('code', '=', 'vnpay'), ('state', '!=', 'disabled')], limit=1)
        if not provider:
            raise UserError(_(
                'Chưa cấu hình cổng thanh toán VNPay (hoặc VNPay đang bị vô '
                'hiệu hoá). Vào Kế toán > Cấu hình > Cổng thanh toán để bật.'))

        tx = self.env['payment.transaction'].sudo().create({
            'provider_id': provider.id,
            'payment_method_id': self.env.ref(
                'fabric_cutting_management.payment_method_vnpay').id,
            'amount': self.deposit_invoice_id.amount_residual,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'invoice_ids': [(6, 0, self.deposit_invoice_id.ids)],
            'reference': self.env['payment.transaction']._compute_reference(
                provider.code, prefix=_('COC-%s') % self.name),
        })
        rendering_values = tx._get_specific_rendering_values({})
        return {
            'type': 'ir.actions.act_url',
            'url': rendering_values['api_url'],
            'target': 'self',
        }

    # Nhóm 7: nút "Thanh toán cho NCC" - tạo payment.transaction VNPay cho
    # đúng số tiền còn nợ (amount_residual) và LIÊN KẾT với (các) hoá đơn NCC
    # posted chưa thanh toán hết của PO này thông qua invoice_ids. Việc gán
    # invoice_ids là bắt buộc để Odoo core tự động tạo account.payment và
    # reconcile với hoá đơn khi tx chuyển 'done' (xem payment.transaction
    # ._post_process()/._create_payments() ở module payment/account gốc) -
    # nếu không set field này, VNPay báo thành công nhưng payment_state của
    # PO/hoá đơn sẽ KHÔNG bao giờ tự cập nhật thành 'paid'.
    def action_pay_supplier_vnpay(self):
        self.ensure_one()
        # Kiểm tra lại 2 điều kiện đã dùng để ẩn/hiện nút trên view (xem
        # invisible của button "Thanh toán phần còn lại cho NCC" trong
        # purchase_order_views.xml) - không chỉ dựa vào UI, vì invisible có
        # thể bị bỏ qua (gọi action trực tiếp qua XML-RPC, dev mode...).
        if not self.is_qc_fully_passed:
            raise UserError(_(
                'Chỉ có thể thanh toán phần còn lại cho NCC sau khi TOÀN BỘ '
                'vải nhận từ đơn mua %s đã PASS QC.'
            ) % self.name)
        if not self.deposit_invoice_paid:
            raise UserError(_(
                'Đơn mua %s còn khoản cọc chưa thanh toán xong. Vui lòng '
                'hoàn tất hoá đơn cọc trước khi thanh toán phần còn lại cho NCC.'
            ) % self.name)
        if self.amount_residual <= 0:
            raise UserError(_('Đơn mua này không còn khoản phải trả nào cho NCC.'))

        provider = self.env['payment.provider'].sudo().search(
            [('code', '=', 'vnpay'), ('state', '!=', 'disabled')], limit=1)
        if not provider:
            raise UserError(_(
                'Chưa cấu hình cổng thanh toán VNPay (hoặc VNPay đang bị vô '
                'hiệu hoá). Vào Kế toán > Cấu hình > Cổng thanh toán để bật.'))

        bills = self.invoice_ids.filtered(
            lambda m: m.move_type == 'in_invoice'
            and m.state == 'posted'
            and m.payment_state not in ('paid', 'in_payment', 'reversed')
        )
        if not bills:
            raise UserError(_(
                'Đơn mua chưa có hoá đơn NCC nào (posted) để thanh toán. '
                'Vui lòng tạo hoá đơn trước.'))

        # Cùng lý do như action_pay_deposit_vnpay(): amount_residual/payment_state
        # của các bills chỉ đổi SAU KHI account.payment được tạo & reconcile (qua
        # polling /payment/status hoặc cron _cron_post_process), có độ trễ so với
        # lúc VNPay xác nhận. Chặn thêm dựa trực tiếp vào payment.transaction
        # đang treo cho các bills này, để tránh tạo 2 giao dịch VNPay cho cùng
        # khoản còn lại nếu người dùng bấm nút 2 lần liên tiếp.
        pending_tx = self.env['payment.transaction'].sudo().search([
            ('invoice_ids', 'in', bills.ids),
            ('state', 'in', ('pending', 'authorized', 'done')),
        ], limit=1)
        if pending_tx:
            raise UserError(_(
                'Đã có giao dịch thanh toán (%(ref)s) đang xử lý hoặc đã thành '
                'công cho (các) hoá đơn còn lại của đơn %(order)s. Vui lòng đợi '
                'xác nhận trước khi tạo giao dịch mới.'
            ) % {'ref': pending_tx.reference, 'order': self.name})

        tx = self.env['payment.transaction'].sudo().create({
            'provider_id': provider.id,
            'payment_method_id': self.env.ref(
                'fabric_cutting_management.payment_method_vnpay').id,
            'amount': self.amount_residual,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'invoice_ids': [(6, 0, bills.ids)],
            'reference': self.env['payment.transaction']._compute_reference(
                provider.code, prefix=self.name),
        })
        rendering_values = tx._get_specific_rendering_values({})
        return {
            'type': 'ir.actions.act_url',
            'url': rendering_values['api_url'],
            'target': 'self',
        }


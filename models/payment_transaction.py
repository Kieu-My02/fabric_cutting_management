import hashlib, hmac, urllib.parse
from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.addons.payment import utils as payment_utils


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _vnpay_build_sign(self, params, hash_secret):
        # QUAN TRỌNG: VNPay yêu cầu chuỗi để ký (hashData) phải là chuỗi
        # key=value đã url-encode (giống hệt cách sẽ xuất hiện trên query
        # string thật), sort theo tên key alphabet. Nếu ký trên giá trị THÔ
        # (chưa encode) trong khi URL thật lại gửi giá trị đã encode (ví dụ
        # khoảng trắng trong vnp_OrderInfo -> '+'), 2 chuỗi sẽ lệch nhau và
        # VNPay tính lại chữ ký ra khác -> báo "Sai chữ ký" (mã lỗi 97).
        sorted_params = sorted(params.items())
        sign_data = '&'.join(
            f'{urllib.parse.quote_plus(str(k))}={urllib.parse.quote_plus(str(v))}'
            for k, v in sorted_params
        )
        return hmac.new(
            hash_secret.encode('utf-8'),
            sign_data.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()

    def _vnpay_get_amount_in_vnd(self):
        """VNPay (kể cả Sandbox) CHỈ thực sự xử lý VND - field vnp_CurrCode
        không phải tham số chọn tiền tệ giao dịch tuỳ ý, chỉ có giá trị
        'VND' được VNPay chấp nhận trong thực tế. Nếu giao dịch (và PO gốc)
        đang ở tiền tệ khác VND (ví dụ USD), phải tự quy đổi sang VND theo
        tỷ giá hiện tại của công ty TRƯỚC khi nhân 100 gửi cho VNPay - nếu
        không, một khoản ví dụ 1,207.50 USD sẽ bị hiểu nhầm thành 1,207 VND,
        sai lệch hoàn toàn giá trị thanh toán."""
        self.ensure_one()
        vnd = self.env['res.currency'].search([('name', '=', 'VND')], limit=1)
        if not vnd:
            raise ValidationError(
                'Không tìm thấy tiền tệ VND trong hệ thống (Kế toán > Cấu '
                'hình > Tiền tệ) - cần kích hoạt VND để thanh toán qua VNPay.')
        if self.currency_id == vnd:
            return self.amount
        return self.currency_id._convert(
            self.amount, vnd, self.company_id, fields.Date.context_today(self))

    def _get_specific_rendering_values(self, processing_values):
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'vnpay':
            return res
        base_url = self.provider_id.get_base_url()
        amount_vnd = self._vnpay_get_amount_in_vnd()
        params = {
            'vnp_Version': '2.1.0',
            'vnp_Command': 'pay',
            'vnp_TmnCode': self.provider_id.vnpay_tmn_code,
            # VNPay tính theo đơn vị xu của VND (nhân 100), KHÔNG dùng số
            # thập phân - dùng round() để tránh sai số float trước khi ép
            # kiểu int.
            'vnp_Amount': int(round(amount_vnd * 100)),
            'vnp_CurrCode': 'VND',
            'vnp_TxnRef': self.reference,
            'vnp_OrderInfo': f'Thanh toan don {self.reference}',
            'vnp_OrderType': 'other',
            'vnp_Locale': 'vn',
            'vnp_ReturnUrl': f'{base_url}/payment/vnpay/return',
            'vnp_IpAddr': payment_utils.get_customer_ip_address() if request else '127.0.0.1',
            'vnp_CreateDate': fields.Datetime.now().strftime('%Y%m%d%H%M%S'),
        }
        params['vnp_SecureHash'] = self._vnpay_build_sign(
            params, self.provider_id.vnpay_hash_secret)
        # Build URL bằng đúng cùng cách encode đã dùng để tính chữ ký ở trên
        # (KHÔNG dùng urllib.parse.urlencode() riêng biệt nữa) - để chuỗi
        # gửi lên VNPay và chuỗi VNPay dùng để tính lại chữ ký hoàn toàn
        # khớp nhau.
        sorted_params = sorted(params.items())
        query = '&'.join(
            f'{urllib.parse.quote_plus(str(k))}={urllib.parse.quote_plus(str(v))}'
            for k, v in sorted_params
        )
        return {'api_url': f'https://sandbox.vnpayment.vn/paymentv2/vpcpay.html?{query}'}

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        if provider_code != 'vnpay':
            return super()._extract_reference(provider_code, payment_data)
        return payment_data.get('vnp_TxnRef')

    def _extract_amount_data(self, payment_data):
        if self.provider_code != 'vnpay':
            return super()._extract_amount_data(payment_data)
        return None  # bỏ qua check amount mặc định của core, vì đã verify qua chữ ký

    def _apply_updates(self, payment_data):
        if self.provider_code != 'vnpay':
            return super()._apply_updates(payment_data)
        # verify chữ ký TRƯỚC khi tin bất kỳ field nào khác
        data_to_verify = {k: v for k, v in payment_data.items() if k != 'vnp_SecureHash'}
        received_hash = payment_data.get('vnp_SecureHash')
        expected = self._vnpay_build_sign(
            data_to_verify, self.provider_id.vnpay_hash_secret)
        if received_hash != expected:
            self._set_error("VNPay: chữ ký không hợp lệ")
            return
        code = payment_data.get('vnp_ResponseCode')
        if code == '00':
            self._set_done()
        else:
            self._set_canceled()

    def _create_payment(self, **extra_create_values):
        # account_payment._create_payment() mặc định hardcode partner_type =
        # 'customer' và payment_type dựa theo dấu của amount (luôn ra
        # 'inbound' vì amount ở đây > 0) - đúng cho chiều KHÁCH HÀNG trả tiền
        # CHO công ty (dùng payment.provider chuẩn cho website_sale/portal).
        # Nhưng action_pay_deposit_vnpay/action_pay_supplier_vnpay lại dùng
        # ngược chiều: CÔNG TY trả tiền CHO nhà cung cấp qua VNPay. Nếu để
        # nguyên mặc định, account.payment được tạo với partner_type/
        # payment_type sai chiều (Phải thu thay vì Phải trả), post() sẽ thất
        # bại (payment kẹt ở draft) và không reconcile được với vendor bill
        # -> deposit_invoice_paid/payment_state không bao giờ tự cập nhật dù
        # VNPay đã xác nhận giao dịch 'done'. Ép đúng chiều 'supplier'/
        # 'outbound' khi hoá đơn liên kết là vendor bill (in_invoice).
        if (
            self.provider_code == 'vnpay'
            and self.invoice_ids
            and self.invoice_ids[0].move_type == 'in_invoice'
        ):
            extra_create_values.setdefault('partner_type', 'supplier')
            extra_create_values.setdefault('payment_type', 'outbound')
        return super()._create_payment(**extra_create_values)
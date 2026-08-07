from odoo import fields, models

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(selection_add=[('vnpay', 'VNPay')],
                             ondelete={'vnpay': 'set default'})
    vnpay_tmn_code = fields.Char(string='VNPay Terminal Code')
    vnpay_hash_secret = fields.Char(string='VNPay Hash Secret')

    def _get_supported_currencies(self):
        supported = super()._get_supported_currencies()
        if self.code == 'vnpay':
            supported = supported.filtered(lambda c: c.name == 'VND')
        return supported

    def _get_default_payment_method_codes(self):
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'vnpay':
            return default_codes
        return ['vnpay']
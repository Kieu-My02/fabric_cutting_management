from odoo import http
from odoo.http import request

class VNPayController(http.Controller):

    @http.route('/payment/vnpay/return', type='http', auth='public', csrf=False)
    def vnpay_return(self, **data):
        request.env['payment.transaction'].sudo()._process('vnpay', data)
        return request.redirect('/payment/status')

    @http.route('/payment/vnpay/ipn', type='http', auth='public', csrf=False)
    def vnpay_ipn(self, **data):
        request.env['payment.transaction'].sudo()._process('vnpay', data)
        return request.make_json_response({'RspCode': '00', 'Message': 'Confirm Success'})
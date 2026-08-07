# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFabricDepositQcFlow(TransactionCase):
    """Nhóm 8 - Cọc mua vải + gate thanh toán phần còn lại theo QC.

    Kiểm tra các kịch bản cốt lõi:
    1) % cọc được gợi ý từ res.partner.fabric_deposit_percent qua onchange,
       không hardcode một tỷ lệ chung cho mọi NCC.
    2) deposit_amount được "đóng băng" đúng một lần tại button_confirm, không
       trôi theo amount_total nếu đơn bị sửa sau khi đã xác nhận.
    3) Hoá đơn cọc được tự tạo ở trạng thái NHÁP, tách biệt khỏi công nợ hàng
       hoá chính thức (invoice_ids/payment_state) của PO.
    4) button_confirm không tạo hoá đơn cọc lần hai nếu đơn bị Huỷ rồi Xác
       nhận lại.
    5) is_qc_fully_passed chỉ True khi TẤT CẢ lot vải nhận từ PO đều PASS.
    6) Nút/action "Thanh toán phần còn lại cho NCC" bị chặn đúng thứ tự:
       chưa PASS QC toàn bộ -> chặn; đã PASS QC nhưng cọc chưa thanh toán
       xong -> vẫn chặn; qua cả hai điều kiện mới xét tới có hoá đơn hàng
       hoá (posted) để thanh toán hay chưa.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.quarantine = cls.env.ref('fabric_cutting_management.location_qc_quarantine')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)

        # Tài khoản kế toán riêng cho test, không phụ thuộc vào chart of
        # accounts demo của công ty (có thể trống/khác nhau tuỳ môi trường).
        cls.expense_account = cls.env['account.account'].create({
            'name': 'Fabric Test Expense',
            'code': 'FABTEST001',
            'account_type': 'expense',
        })
        cls.payable_account = cls.env['account.account'].create({
            'name': 'Fabric Test Payable',
            'code': 'FABTEST002',
            'account_type': 'liability_payable',
            'reconcile': True,
        })
        cls.bank_journal = cls.env['account.journal'].create({
            'name': 'Fabric Test Bank',
            'type': 'bank',
            'code': 'FBNK',
        })

        cls.vendor = cls.env['res.partner'].create({
            'name': 'NCC Test Cọc Vải',
            'fabric_deposit_percent': 40.0,
            'property_account_payable_id': cls.payable_account.id,
        })
        cls.vendor_no_deposit = cls.env['res.partner'].create({
            'name': 'NCC Test Không Cọc',
            'fabric_deposit_percent': 0.0,
            'property_account_payable_id': cls.payable_account.id,
        })

        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Kaki Test Cọc',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 220.0,
            'default_fabric_width': 150.0,
        })
        cls.fabric_product.categ_id.property_account_expense_categ_id = cls.expense_account

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _create_po(self, vendor, qty=100.0, price_unit=10.0):
        return self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.fabric_product.id,
                'product_qty': qty,
                'product_uom': self.fabric_product.uom_id.id,
                'price_unit': price_unit,
                'name': self.fabric_product.name,
            })],
        })

    def _receive_and_qc(self, po, lot_name, qty=None, pass_qc=True):
        """Nhận hàng cho PO đã confirm với 1 lot mới, tuỳ chọn PASS QC luôn."""
        picking = po.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))[:1]
        move = picking.move_ids[0]
        move_qty = qty if qty is not None else move.product_uom_qty
        move.move_line_ids = [(0, 0, {
            'product_id': self.fabric_product.id,
            'lot_name': lot_name,
            'quantity': move_qty,
            'location_id': move.location_id.id,
            'location_dest_id': move.location_dest_id.id,
        })]
        picking.button_validate()
        lot = self.env['stock.lot'].search([
            ('name', '=', lot_name), ('product_id', '=', self.fabric_product.id),
        ], limit=1)
        if pass_qc:
            lot.action_set_qc_pass()
        return lot

    def _pay_deposit_invoice_in_full(self, po):
        """Post + tất toán hoá đơn cọc của PO để mô phỏng Kế toán đã xử lý xong cọc."""
        invoice = po.deposit_invoice_id
        invoice.action_post()
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({'journal_id': self.bank_journal.id})
        register._create_payments()
        return invoice

    # ------------------------------------------------------------------
    # 1) Gợi ý % cọc từ NCC (không hardcode)
    # ------------------------------------------------------------------
    def test_deposit_percent_defaults_from_partner_and_is_editable(self):
        with Form(self.env['purchase.order']) as po_form:
            po_form.partner_id = self.vendor
            self.assertEqual(
                po_form.deposit_percent, 40.0,
                'deposit_percent phải được gợi ý từ fabric_deposit_percent của NCC.')
            # Thu mua vẫn sửa tay được cho riêng đơn này.
            po_form.deposit_percent = 25.0

        with Form(self.env['purchase.order']) as po_form2:
            po_form2.partner_id = self.vendor_no_deposit
            self.assertEqual(
                po_form2.deposit_percent, 0.0,
                'NCC khác nhau phải có % cọc gợi ý khác nhau, không hardcode chung.')

    # ------------------------------------------------------------------
    # 2) deposit_amount đóng băng tại button_confirm
    # ------------------------------------------------------------------
    def test_deposit_amount_frozen_at_confirm(self):
        po = self._create_po(self.vendor, qty=100.0, price_unit=10.0)
        po.deposit_percent = 40.0
        po.button_confirm()

        expected = po.amount_total * 0.40
        self.assertAlmostEqual(po.deposit_amount, expected)

        # Sửa số lượng dòng đặt hàng SAU khi đã confirm (ví dụ phụ phí phát
        # sinh) - amount_total đổi nhưng deposit_amount phải giữ nguyên.
        po.order_line.product_qty = 150.0
        self.assertNotAlmostEqual(po.amount_total, expected / 0.40)
        self.assertAlmostEqual(
            po.deposit_amount, expected,
            msg='deposit_amount phải giữ nguyên, không trôi theo amount_total sau khi confirm.')

    def test_no_deposit_amount_when_partner_has_zero_percent(self):
        po = self._create_po(self.vendor_no_deposit)
        po.button_confirm()
        self.assertEqual(po.deposit_percent, 0.0)
        self.assertEqual(po.deposit_amount, 0.0)
        self.assertFalse(po.deposit_invoice_id, 'Không yêu cầu cọc thì không tạo hoá đơn cọc.')

    # ------------------------------------------------------------------
    # 3) Hoá đơn cọc tự tạo ở trạng thái NHÁP, tách biệt công nợ hàng hoá
    # ------------------------------------------------------------------
    def test_deposit_invoice_created_as_draft_and_not_auto_posted(self):
        po = self._create_po(self.vendor)
        po.deposit_percent = 40.0
        po.button_confirm()

        invoice = po.deposit_invoice_id
        self.assertTrue(invoice, 'Phải tự sinh hoá đơn cọc khi confirm.')
        self.assertEqual(
            invoice.state, 'draft',
            'Hoá đơn cọc phải dừng ở NHÁP, Kế toán vẫn phải tự Post/duyệt.')
        self.assertEqual(invoice.move_type, 'in_invoice')
        self.assertAlmostEqual(invoice.amount_total, po.deposit_amount)

    def test_deposit_invoice_separate_from_goods_payment_state(self):
        po = self._create_po(self.vendor)
        po.deposit_percent = 40.0
        po.button_confirm()
        self._pay_deposit_invoice_in_full(po)

        # Cọc đã thanh toán xong nhưng KHÔNG được tính vào công nợ hàng hoá
        # chính thức của PO (payment_state/invoice_ids) - tránh nhầm lẫn
        # giữa 2 loại công nợ.
        self.assertNotIn(po.deposit_invoice_id, po.invoice_ids)
        self.assertEqual(
            po.payment_state, 'not_billed',
            'Chưa có hoá đơn hàng hoá chính thức nào, hoá đơn cọc không được tính vào đây.')

    # ------------------------------------------------------------------
    # 4) Không tạo hoá đơn cọc thứ hai khi Huỷ rồi Xác nhận lại
    # ------------------------------------------------------------------
    def test_no_duplicate_deposit_invoice_on_cancel_and_reconfirm(self):
        po = self._create_po(self.vendor)
        po.deposit_percent = 40.0
        po.button_confirm()

        first_invoice = po.deposit_invoice_id
        first_amount = po.deposit_amount
        self.assertTrue(first_invoice)

        po.button_cancel()
        po.button_draft()
        po.button_confirm()

        self.assertEqual(
            po.deposit_invoice_id, first_invoice,
            'Xác nhận lại sau khi Huỷ không được tạo hoá đơn cọc thứ hai.')
        self.assertAlmostEqual(
            po.deposit_amount, first_amount,
            msg='deposit_amount đã chốt trước đó không được tính lại.')

        deposit_moves = self.env['account.move'].search([
            ('invoice_origin', '=', po.name), ('move_type', '=', 'in_invoice'),
        ])
        self.assertEqual(
            len(deposit_moves), 1,
            'Chỉ được đúng 1 hoá đơn cọc cho mỗi PO, kể cả sau khi Huỷ/Xác nhận lại.')

    # ------------------------------------------------------------------
    # 5) is_qc_fully_passed xét trên TOÀN BỘ lot, không phải từng lot riêng
    # ------------------------------------------------------------------
    def test_is_qc_fully_passed_false_when_no_lot_received_yet(self):
        po = self._create_po(self.vendor)
        po.button_confirm()
        self.assertFalse(po.is_qc_fully_passed)

    def test_is_qc_fully_passed_false_when_partially_passed(self):
        po = self._create_po(self.vendor, qty=100.0)
        po.order_line.write({'product_qty': 100.0})
        po.button_confirm()
        # Chia làm 2 lần nhận hàng riêng biệt trên cùng PO để có 2 lot.
        self._receive_and_qc(po, 'ROLL-DEP-001', qty=100.0, pass_qc=True)

        # Thêm một dòng đặt hàng khác để tạo lot thứ hai còn "Chờ kiểm".
        po.write({'order_line': [(0, 0, {
            'product_id': self.fabric_product.id,
            'product_qty': 50.0,
            'product_uom': self.fabric_product.uom_id.id,
            'price_unit': 10.0,
            'name': self.fabric_product.name,
        })]})
        self._receive_and_qc(po, 'ROLL-DEP-002', qty=50.0, pass_qc=False)

        self.assertFalse(
            po.is_qc_fully_passed,
            'Còn 1 lot Chờ kiểm/FAIL thì cả PO vẫn coi là CHƯA đạt.')

    def test_is_qc_fully_passed_true_when_all_lots_pass(self):
        po = self._create_po(self.vendor, qty=100.0)
        po.button_confirm()
        self._receive_and_qc(po, 'ROLL-DEP-003', qty=100.0, pass_qc=True)
        self.assertTrue(po.is_qc_fully_passed)

    # ------------------------------------------------------------------
    # 6) Gate của action_pay_supplier_vnpay: đúng thứ tự QC -> cọc -> hoá đơn
    # ------------------------------------------------------------------
    def test_pay_supplier_blocked_when_qc_not_fully_passed(self):
        po = self._create_po(self.vendor)
        po.deposit_percent = 40.0
        po.button_confirm()
        # Chưa nhận/QC lot nào -> is_qc_fully_passed = False.
        with self.assertRaises(UserError):
            po.action_pay_supplier_vnpay()

    def test_pay_supplier_blocked_when_deposit_not_paid_even_if_qc_passed(self):
        po = self._create_po(self.vendor)
        po.deposit_percent = 40.0
        po.button_confirm()
        self._receive_and_qc(po, 'ROLL-DEP-004', pass_qc=True)
        self.assertTrue(po.is_qc_fully_passed)
        # deposit_invoice_id tồn tại nhưng chưa post/thanh toán.
        self.assertFalse(po.deposit_invoice_paid)

        with self.assertRaises(UserError):
            po.action_pay_supplier_vnpay()

    def test_pay_supplier_passes_qc_and_deposit_gate_then_blocks_on_missing_bill(self):
        po = self._create_po(self.vendor)
        po.deposit_percent = 40.0
        po.button_confirm()
        self._receive_and_qc(po, 'ROLL-DEP-005', pass_qc=True)
        self._pay_deposit_invoice_in_full(po)

        self.assertTrue(po.is_qc_fully_passed)
        self.assertTrue(po.deposit_invoice_paid)

        # Đã qua được 2 điều kiện QC + cọc, nhưng PO chưa có hoá đơn hàng
        # hoá (posted) nào để thanh toán phần còn lại -> vẫn phải chặn,
        # với thông báo khác (không còn liên quan tới QC/cọc nữa).
        with self.assertRaises(UserError):
            po.action_pay_supplier_vnpay()

# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFabricQcPassFlow(TransactionCase):
    """Nhóm 1 - Trừ tồn theo QC Pass.

    Kiểm tra 3 kịch bản cốt lõi:
    1) PASS -> tự động tạo dịch chuyển nội bộ, đưa hàng từ Khu chờ QC vào
       kho chính, đúng số lượng.
    2) FAIL lại sau khi đã PASS, nhưng hàng vẫn còn nguyên trong kho chính
       -> tự động hoàn ngược về Khu chờ QC.
    3) FAIL lại sau khi đã PASS, nhưng một phần hàng đã bị tiêu thụ (đã xuất
       cho sản xuất) -> phải bị chặn (UserError), không được âm thầm đảo
       ngược một phần dữ liệu không còn khớp thực tế.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.quarantine = cls.env.ref('fabric_cutting_management.location_qc_quarantine')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.vendor = cls.env['res.partner'].create({'name': 'NCC Test Vải'})

        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Kaki Test',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 220.0,
            'default_fabric_width': 150.0,
        })

    def _receive_po(self, qty, lot_name):
        """Tạo + xác nhận PO, nhận hàng với 1 lot mới, trả về stock.lot đã tạo."""
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.fabric_product.id,
                'product_qty': qty,
                'product_uom': self.fabric_product.uom_id.id,
                'price_unit': 10.0,
                'name': self.fabric_product.name,
            })],
        })
        po.button_confirm()

        picking = po.picking_ids
        self.assertTrue(picking, 'PO xác nhận phải sinh ra phiếu nhập kho.')
        move = picking.move_ids[0]
        # Nhóm 1: ngay sau khi PO confirm, đích đến của move phải là Khu chờ QC.
        self.assertEqual(move.location_dest_id, self.quarantine)

        move.move_line_ids = [(0, 0, {
            'product_id': self.fabric_product.id,
            'lot_name': lot_name,
            'quantity': qty,
            'location_id': move.location_id.id,
            'location_dest_id': move.location_dest_id.id,
        })]
        picking.button_validate()

        lot = self.env['stock.lot'].search([
            ('name', '=', lot_name), ('product_id', '=', self.fabric_product.id),
        ], limit=1)
        self.assertTrue(lot, 'Phải tạo được stock.lot sau khi nhận hàng.')
        return lot

    def _qty_at(self, lot, location):
        return sum(lot.quant_ids.filtered(lambda q: q.location_id == location).mapped('quantity'))

    def test_receipt_lands_in_quarantine_not_main_stock(self):
        lot = self._receive_po(100.0, 'ROLL-TEST-001')
        self.assertEqual(lot.qc_state, 'pending')
        self.assertEqual(self._qty_at(lot, self.quarantine), 100.0)
        self.assertEqual(self._qty_at(lot, self.warehouse.lot_stock_id), 0.0)

    def test_qc_pass_releases_stock_to_main_warehouse(self):
        lot = self._receive_po(100.0, 'ROLL-TEST-002')
        lot.action_set_qc_pass()

        self.assertEqual(lot.qc_state, 'pass')
        self.assertEqual(self._qty_at(lot, self.quarantine), 0.0,
                          'Sau PASS, Khu chờ QC phải hết hàng của lot này.')
        self.assertEqual(self._qty_at(lot, self.warehouse.lot_stock_id), 100.0,
                          'Sau PASS, toàn bộ số lượng phải nằm ở kho chính.')

    def test_qc_fail_after_pass_reverses_when_untouched(self):
        lot = self._receive_po(100.0, 'ROLL-TEST-003')
        lot.action_set_qc_pass()

        lot.action_set_qc_fail()

        self.assertEqual(lot.qc_state, 'fail')
        self.assertEqual(self._qty_at(lot, self.warehouse.lot_stock_id), 0.0,
                          'FAIL lại khi chưa tiêu thụ gì phải hoàn ngược toàn bộ về Khu chờ QC.')
        self.assertEqual(self._qty_at(lot, self.quarantine), 100.0)

    def test_qc_fail_after_pass_blocked_when_partially_consumed(self):
        lot = self._receive_po(100.0, 'ROLL-TEST-004')
        lot.action_set_qc_pass()

        # Giả lập Lệnh Cắt đã tiêu thụ một phần: xuất 40 đơn vị ra vị trí ảo
        # "Production" (usage='production'), đúng cách MO thật sự tiêu thụ
        # nguyên liệu trong Odoo.
        production_location = self.env.ref('stock.location_production')
        consume_move = self.env['stock.move'].create({
            'description_picking': 'Test tiêu thụ 1 phần cho Lệnh Cắt',
            'product_id': self.fabric_product.id,
            'product_uom_qty': 40.0,
            'product_uom': self.fabric_product.uom_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': production_location.id,
        })
        consume_move._action_confirm()
        consume_move._action_assign()
        consume_move.move_line_ids = [(0, 0, {
            'product_id': self.fabric_product.id,
            'lot_id': lot.id,
            'quantity': 40.0,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': production_location.id,
        })]
        consume_move.move_line_ids.write({'picked': True})
        consume_move._action_done()

        self.assertEqual(self._qty_at(lot, self.warehouse.lot_stock_id), 60.0)

        with self.assertRaises(UserError):
            lot.action_set_qc_fail()

        # Trạng thái và tồn kho không được thay đổi sau khi bị chặn.
        self.assertEqual(lot.qc_state, 'pass')
        self.assertEqual(self._qty_at(lot, self.warehouse.lot_stock_id), 60.0)

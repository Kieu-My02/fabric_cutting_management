# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFabricConsolidatedPOWizard(TransactionCase):
    """Nhóm 4 - Gộp nhiều đơn cùng mã vải/màu/sản phẩm -> 1 PO tổng.

    Kịch bản chính: 2 Đơn hàng bán cùng cần một mã vải/màu (thông qua BoM),
    cộng với sẵn 30m tồn khả dụng đã QC PASS trong kho -> wizard phải cộng
    dồn đúng tổng nhu cầu, trừ đúng tồn hiện có, và sinh ra đúng 1 PO duy
    nhất cho phần còn thiếu.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'NCC Test Nhóm 4'})
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Kaki Nhóm 4 - Xanh Rêu',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 220.0,
        })
        cls.fabric_product.seller_ids = [(0, 0, {
            'partner_id': cls.vendor.id,
            'price': 12.5,
        })]
        cls.finished_product = cls.env['product.product'].create({
            'name': 'Quần Kaki Test Nhóm 4',
            'type': 'consu',
            'is_storable': True,
        })
        cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished_product.product_tmpl_id.id,
            'product_id': cls.finished_product.id,
            'product_qty': 1.0,
            'product_uom_id': cls.finished_product.uom_id.id,
            'type': 'normal',
            'bom_line_ids': [(0, 0, {
                'product_id': cls.fabric_product.id,
                'product_qty': 1.5,
                'product_uom_id': cls.fabric_product.uom_id.id,
            })],
        })
        cls.customer = cls.env['res.partner'].create({'name': 'KH Test Nhóm 4'})
        cls.order_1 = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'order_line': [(0, 0, {
                'product_id': cls.finished_product.id,
                'product_uom_qty': 10.0,  # -> 15m vải
            })],
        })
        cls.order_2 = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'order_line': [(0, 0, {
                'product_id': cls.finished_product.id,
                'product_uom_qty': 20.0,  # -> 30m vải
            })],
        })

        # 30m tồn khả dụng đã QC PASS sẵn có trong kho chính.
        warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        lot = cls.env['stock.lot'].create({
            'name': 'ROLL-NHOM4-001',
            'product_id': cls.fabric_product.id,
            'qc_state': 'pass',
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.fabric_product, warehouse.lot_stock_id, 30.0, lot_id=lot)

    def _new_wizard(self, sale_orders):
        return self.env['fabric.consolidated.po.wizard'].create({
            'sale_order_ids': [(6, 0, sale_orders.ids)],
        })

    def test_compute_lines_aggregates_and_subtracts_onhand(self):
        wizard = self._new_wizard(self.order_1 | self.order_2)
        wizard.action_compute_lines()

        self.assertEqual(len(wizard.line_ids), 1,
                          'Chỉ có 1 mã vải/màu -> phải gộp về đúng 1 dòng.')
        line = wizard.line_ids[0]
        self.assertEqual(line.product_id, self.fabric_product)
        self.assertAlmostEqual(line.qty_needed, 45.0, places=2,
                                msg='15m (đơn 1) + 30m (đơn 2) = 45m tổng nhu cầu.')
        self.assertAlmostEqual(line.onhand_qty, 30.0, places=2)
        self.assertAlmostEqual(line.qty_to_order, 15.0, places=2,
                                msg='45m nhu cầu - 30m tồn = 15m cần đặt mua.')
        self.assertEqual(line.partner_id, self.vendor,
                          'Phải tự gợi ý đúng NCC chính khai báo trên mã vải.')

    def test_create_purchase_orders_creates_single_po(self):
        wizard = self._new_wizard(self.order_1 | self.order_2)
        wizard.action_compute_lines()

        action = wizard.action_create_purchase_orders()

        self.assertEqual(action['res_model'], 'purchase.order')
        po = self.env['purchase.order'].browse(action['res_id'])
        self.assertEqual(po.partner_id, self.vendor)
        self.assertEqual(len(po.order_line), 1)
        self.assertAlmostEqual(po.order_line.product_qty, 15.0, places=2)
        self.assertIn(self.order_1.name, po.origin)
        self.assertIn(self.order_2.name, po.origin)

    def test_no_shortage_raises_user_error(self):
        wizard = self._new_wizard(self.order_1 | self.order_2)
        wizard.action_compute_lines()
        wizard.line_ids.qty_to_order = 0.0

        with self.assertRaises(UserError):
            wizard.action_create_purchase_orders()

    def test_compute_lines_without_selection_raises(self):
        wizard = self.env['fabric.consolidated.po.wizard'].create({})
        with self.assertRaises(UserError):
            wizard.action_compute_lines()

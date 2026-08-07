# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleOrderFabricNeed(TransactionCase):
    """FR-14 - Nhập mã đơn hàng -> tính vải cần.

    Kịch bản: 1 sản phẩm may thành phẩm có BoM gồm 2 dòng: 1.5m vải/áo (mã
    vải, theo dõi bằng Lot + có GSM) và 0.2m chỉ may/áo (không phải vải -
    không theo dõi bằng Lot). Đặt 10 áo trên 1 đơn hàng -> phải ra đúng 1
    dòng nhu cầu vải = 15m, và KHÔNG có dòng nào cho chỉ may."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Kaki Đơn hàng - Xanh Rêu',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 220.0,
        })
        cls.thread_product = cls.env['product.product'].create({
            'name': 'Chỉ may Test',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'none',
        })
        cls.finished_product = cls.env['product.product'].create({
            'name': 'Áo Sơ Mi Test',
            'type': 'consu',
            'is_storable': True,
        })
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished_product.product_tmpl_id.id,
            'product_id': cls.finished_product.id,
            'product_qty': 1.0,
            'product_uom_id': cls.finished_product.uom_id.id,
            'type': 'normal',
            'bom_line_ids': [
                (0, 0, {
                    'product_id': cls.fabric_product.id,
                    'product_qty': 1.5,
                    'product_uom_id': cls.fabric_product.uom_id.id,
                }),
                (0, 0, {
                    'product_id': cls.thread_product.id,
                    'product_qty': 0.2,
                    'product_uom_id': cls.thread_product.uom_id.id,
                }),
            ],
        })
        cls.customer = cls.env['res.partner'].create({'name': 'KH Test FR-14'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'order_line': [(0, 0, {
                'product_id': cls.finished_product.id,
                'product_uom_qty': 10.0,
            })],
        })

    def test_compute_fabric_requirement_only_fabric_lines(self):
        self.order.action_compute_fabric_requirement()

        self.assertEqual(len(self.order.fabric_need_ids), 1,
                          'Chỉ dòng vải mới được đưa vào nhu cầu, chỉ may phải bị loại.')
        need = self.order.fabric_need_ids[0]
        self.assertEqual(need.product_id, self.fabric_product)
        self.assertAlmostEqual(need.qty_needed, 15.0, places=2,
                                msg='10 áo x 1.5m vải/áo phải ra tổng 15m.')

    def test_recompute_replaces_old_lines(self):
        self.order.action_compute_fabric_requirement()
        self.order.order_line.product_uom_qty = 20.0
        self.order.action_compute_fabric_requirement()

        self.assertEqual(len(self.order.fabric_need_ids), 1,
                          'Bấm lại nút không được cộng dồn/nhân đôi dòng cũ.')
        self.assertAlmostEqual(self.order.fabric_need_ids[0].qty_needed, 30.0, places=2)

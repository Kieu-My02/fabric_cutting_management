# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPurchaseOrderPaymentState(TransactionCase):
    """Nhóm 7 - Thanh toán mua-bán: purchase.order.payment_state phải phản
    ánh đúng tình trạng công nợ phải trả từ hoá đơn NCC (account.move,
    move_type='in_invoice', đã posted) - đối xứng với sale.order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'NCC Test Nhóm 7'})
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Test Nhóm 7',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 200.0,
        })
        cls.po = cls.env['purchase.order'].create({
            'partner_id': cls.vendor.id,
            'order_line': [(0, 0, {
                'product_id': cls.fabric_product.id,
                'product_qty': 100.0,
                'product_uom': cls.fabric_product.uom_id.id,
                'price_unit': 50000.0,
                'name': cls.fabric_product.name,
            })],
        })
        cls.po.button_confirm()

    def test_not_billed_before_any_bill(self):
        self.assertEqual(self.po.payment_state, 'not_billed')
        self.assertEqual(self.po.amount_billed, 0.0)
        self.assertEqual(self.po.amount_residual, 0.0)

    def test_not_paid_after_posting_bill(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.fabric_product.id,
                'quantity': 100.0,
                'price_unit': 50000.0,
            })],
        })
        bill.action_post()
        self.po.invalidate_recordset(['payment_state', 'amount_billed', 'amount_residual'])

        self.assertEqual(self.po.payment_state, 'not_paid')
        self.assertAlmostEqual(self.po.amount_billed, bill.amount_total, places=2)
        self.assertAlmostEqual(self.po.amount_residual, bill.amount_total, places=2)

    def test_draft_bill_is_ignored(self):
        self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.fabric_product.id,
                'quantity': 100.0,
                'price_unit': 50000.0,
            })],
        })
        self.po.invalidate_recordset(['payment_state'])

        self.assertEqual(self.po.payment_state, 'not_billed',
                          'Hoá đơn còn ở trạng thái Nháp (chưa posted) không được tính vào công nợ.')


@tagged('post_install', '-at_install')
class TestSaleOrderPurchaseOutboundFabricSummary(TransactionCase):
    """Nhóm 7-9: liên kết field + tổng hợp giữa sale.order, purchase.order,
    account.move và stock.picking - không tạo model mới, chỉ suy ra từ dữ
    liệu đã có (mrp.production.sale_order_id của Nhóm 5-6 làm trục chính)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'NCC Test Nhóm 8-9'})
        cls.customer = cls.env['res.partner'].create({'name': 'KH Test Nhóm 8-9'})
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Test Nhóm 8-9',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 200.0,
        })
        cls.finished_product = cls.env['product.product'].create({
            'name': 'Áo Test Nhóm 8-9',
            'type': 'consu',
            'is_storable': True,
        })
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'order_line': [(0, 0, {
                'product_id': cls.finished_product.id,
                'product_uom_qty': 10.0,
            })],
        })

        # Giả lập 1 Lệnh Cắt (mrp.production) sinh ra từ đơn hàng này - gán
        # thẳng sale_order_id (field compute+store của Nhóm 5-6) như cách
        # test_qc_pass_flow.py mô phỏng move tiêu thụ trực tiếp, tránh phải
        # dựng nguyên dây chuyền MTO đầy đủ chỉ để kiểm tra logic tổng hợp.
        cls.production = cls.env['mrp.production'].create({
            'product_id': cls.finished_product.id,
            'product_qty': 10.0,
            'product_uom_id': cls.finished_product.uom_id.id,
        })
        cls.production.write({'sale_order_id': cls.order.id})

    def test_purchase_order_ids_linked_via_production(self):
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {
                'product_id': self.fabric_product.id,
                'product_qty': 15.0,
                'product_uom': self.fabric_product.uom_id.id,
                'price_unit': 50000.0,
                'name': self.fabric_product.name,
                'production_id': self.production.id,
            })],
        })
        po.button_confirm()
        self.order.invalidate_recordset(['purchase_order_ids', 'purchase_order_count', 'purchase_payment_state'])

        self.assertEqual(self.order.purchase_order_count, 1)
        self.assertEqual(self.order.purchase_order_ids, po)
        self.assertEqual(self.order.purchase_payment_state, 'not_billed')

    def test_no_purchase_order_state_is_none(self):
        self.assertEqual(self.order.purchase_order_count, 0)
        self.assertEqual(self.order.purchase_payment_state, 'none')

    def _create_fabric_release_picking_type(self):
        """Mô phỏng picking type 'cấp phát vải cho Phòng Cắt': code=outgoing,
        nguồn là vị trí nội bộ - đúng tiêu chí is_fabric_release (stock_picking.py)."""
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        return self.env['stock.picking.type'].create({
            'name': 'Cấp phát vải Test Nhóm 8',
            'code': 'outgoing',
            'sequence_code': 'FABTEST',
            'warehouse_id': warehouse.id,
            'default_location_src_id': warehouse.lot_stock_id.id,
            'default_location_dest_id': self.env.ref('stock.location_production').id,
        })

    def test_fabric_outbound_pending_when_no_release_picking(self):
        self.assertEqual(self.order.fabric_release_count, 0)
        self.assertEqual(self.order.fabric_outbound_state, 'pending')

    def test_fabric_outbound_done_when_release_picking_validated(self):
        picking_type = self._create_fabric_release_picking_type()
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        self.assertTrue(picking.is_fabric_release,
                         'Picking mô phỏng phải được nhận diện là cấp phát vải cho Phòng Cắt.')

        move = self.env['stock.move'].create({
            'description_picking': 'Test cấp vải cho Lệnh Cắt Nhóm 8',
            'product_id': self.fabric_product.id,
            'product_uom_qty': 15.0,
            'product_uom': self.fabric_product.uom_id.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'picking_id': picking.id,
            'raw_material_production_id': self.production.id,
        })
        move._action_confirm()

        self.order.invalidate_recordset(
            ['fabric_release_picking_ids', 'fabric_release_count', 'fabric_outbound_state'])
        self.assertEqual(self.order.fabric_release_count, 1)
        self.assertEqual(self.order.fabric_outbound_state, 'partial',
                          'Phiếu chưa done thì phải là "Đang xuất một phần", chưa phải "Đã xuất xong".')

        lot = self.env['stock.lot'].create({
            'name': 'ROLL-NHOM8-001',
            'product_id': self.fabric_product.id,
        })
        lot.qc_state = 'pass'
        move.move_line_ids = [(0, 0, {
            'product_id': self.fabric_product.id,
            'lot_id': lot.id,
            'quantity': 15.0,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })]
        move.move_line_ids.write({'picked': True})
        picking.button_validate()

        self.order.invalidate_recordset(['fabric_outbound_state'])
        self.assertEqual(self.order.fabric_outbound_state, 'done')


@tagged('post_install', '-at_install')
class TestSaleOrderFabricNeedActualQty(TransactionCase):
    """Nhóm 9 - Tổng vải cho đơn may: sale.order.fabric.need.qty_issued phải
    cộng dồn đúng số lượng thực tế đã xuất (move đã "picked") từ TẤT CẢ Lệnh
    Cắt của đơn hàng, cho đúng mã vải/màu - và tính được chênh lệch so với
    nhu cầu lý thuyết (FR-14)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'KH Test Nhóm 9'})
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Test Nhóm 9',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 200.0,
        })
        cls.thread_product = cls.env['product.product'].create({
            'name': 'Chỉ may Test Nhóm 9',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'none',
        })
        cls.finished_product = cls.env['product.product'].create({
            'name': 'Áo Test Nhóm 9',
            'type': 'consu',
            'is_storable': True,
        })
        cls.env['mrp.bom'].create({
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
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'order_line': [(0, 0, {
                'product_id': cls.finished_product.id,
                'product_uom_qty': 10.0,
            })],
        })
        cls.order.action_compute_fabric_requirement()
        cls.need = cls.order.fabric_need_ids

        cls.production = cls.env['mrp.production'].create({
            'product_id': cls.finished_product.id,
            'product_qty': 10.0,
            'product_uom_id': cls.finished_product.uom_id.id,
        })
        cls.production.write({'sale_order_id': cls.order.id})
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)

    def test_qty_issued_zero_before_any_consumption(self):
        self.assertAlmostEqual(self.need.qty_needed, 15.0, places=2)
        self.assertEqual(self.need.qty_issued, 0.0)
        self.assertAlmostEqual(self.need.qty_variance, -15.0, places=2)

    def test_qty_issued_aggregates_picked_raw_moves(self):
        move = self.env['stock.move'].create({
            'description_picking': 'Test tiêu thụ vải Nhóm 9',
            'product_id': self.fabric_product.id,
            'product_uom_qty': 15.0,
            'product_uom': self.fabric_product.uom_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.location_production').id,
            'raw_material_production_id': self.production.id,
        })
        move._action_confirm()
        move._action_assign()
        lot = self.env['stock.lot'].create({
            'name': 'ROLL-NHOM9-001',
            'product_id': self.fabric_product.id,
        })
        move.move_line_ids = [(0, 0, {
            'product_id': self.fabric_product.id,
            'lot_id': lot.id,
            'quantity': 12.0,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.location_production').id,
        })]
        move.move_line_ids.write({'picked': True})
        move._action_done()

        self.need.invalidate_recordset(['qty_issued', 'qty_variance', 'qty_variance_percent'])
        self.assertAlmostEqual(self.need.qty_issued, 12.0, places=2,
                                msg='Chỉ tính move đã "picked", đúng bằng số lượng thực xuất.')
        self.assertAlmostEqual(self.need.qty_variance, -3.0, places=2,
                                msg='12m thực xuất - 15m nhu cầu = -3m (còn thiếu 3m).')
        self.assertAlmostEqual(self.need.qty_variance_percent, -20.0, places=1)

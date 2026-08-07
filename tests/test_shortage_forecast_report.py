# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo.fields import Datetime
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFabricShortageForecastReport(TransactionCase):
    """FR-13 - Dự báo thiếu vải từ lịch sử nhập/xuất.

    Kịch bản: nhận 1 cây vải 300m, cho PASS QC (để tính vào tồn khả dụng),
    rồi giả lập 2 lần xuất cho Lệnh Cắt cách nhau 10 ngày (tổng 100m tiêu
    thụ trong 10 ngày -> tốc độ TB 10m/ngày). Với lead time + safety stock
    mặc định (15 + 7 = 22 ngày) thì điểm đặt hàng lại phải là 220m, vượt xa
    tồn khả dụng còn lại (200m) -> phải lên cảnh báo thiếu vải."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.vendor = cls.env['res.partner'].create({'name': 'NCC Test Dự báo'})
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Kaki Dự báo',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'is_fabric': True,
            'default_fabric_gsm': 220.0,
            'default_fabric_width': 150.0,
        })

        po = cls.env['purchase.order'].create({
            'partner_id': cls.vendor.id,
            'order_line': [(0, 0, {
                'product_id': cls.fabric_product.id,
                'product_qty': 300.0,
                'product_uom': cls.fabric_product.uom_id.id,
                'price_unit': 5.0,
                'name': cls.fabric_product.name,
            })],
        })
        po.button_confirm()
        picking = po.picking_ids
        move = picking.move_ids[0]
        move.move_line_ids = [(0, 0, {
            'product_id': cls.fabric_product.id,
            'lot_name': 'ROLL-FORECAST-001',
            'quantity': 300.0,
            'location_id': move.location_id.id,
            'location_dest_id': move.location_dest_id.id,
        })]
        picking.button_validate()
        cls.lot = cls.env['stock.lot'].search([
            ('name', '=', 'ROLL-FORECAST-001'), ('product_id', '=', cls.fabric_product.id),
        ], limit=1)
        cls.lot.action_set_qc_pass()

        cls.production_location = cls.env.ref('stock.location_production')
        cls._consume(cls, 50.0, days_ago=10)
        cls._consume(cls, 50.0, days_ago=0)

    def _consume(self, qty, days_ago):
        """Tạo + hoàn tất 1 move xuất dùng cho Lệnh Cắt, sau đó chỉnh lại
        ngày (date) của move về quá khứ `days_ago` ngày bằng SQL trực tiếp,
        để mô phỏng lịch sử tiêu thụ trải dài nhiều ngày trong test."""
        move = self.env['stock.move'].create({
            'description_picking': 'Test tiêu thụ dự báo',
            'product_id': self.fabric_product.id,
            'product_uom_qty': qty,
            'product_uom': self.fabric_product.uom_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.production_location.id,
        })
        move._action_confirm()
        move._action_assign()
        move.move_line_ids = [(0, 0, {
            'product_id': self.fabric_product.id,
            'lot_id': self.lot.id,
            'quantity': qty,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.production_location.id,
        })]
        move.move_line_ids.write({'picked': True})
        move._action_done()

        backdated = Datetime.now() - timedelta(days=days_ago)
        self.env.cr.execute(
            'UPDATE stock_move SET date = %s WHERE id = %s', (backdated, move.id))
        move.invalidate_recordset(['date'])

    def test_shortage_forecast_report_computes_expected_values(self):
        report = self.env['fabric.shortage.forecast.report'].search([
            ('product_id', '=', self.fabric_product.id),
        ])
        self.assertEqual(len(report), 1, 'Phải gộp về đúng 1 dòng cho mã vải này.')
        line = report[0]

        self.assertAlmostEqual(line.total_out_qty, 100.0, places=2)
        self.assertAlmostEqual(line.days_history, 10.0, places=1)
        self.assertAlmostEqual(line.avg_daily_consumption, 10.0, places=2)
        self.assertAlmostEqual(line.onhand_qty, 200.0, places=2)
        self.assertAlmostEqual(line.lead_time_days, 15.0, places=2)
        self.assertAlmostEqual(line.safety_stock_days, 7.0, places=2)
        self.assertAlmostEqual(line.safety_stock_qty, 70.0, places=2)
        self.assertAlmostEqual(line.reorder_point_qty, 220.0, places=2)
        self.assertAlmostEqual(line.qty_to_order, 20.0, places=2)
        self.assertTrue(line.shortage_alert, 'Tồn 200m < điểm đặt hàng lại 220m -> phải cảnh báo.')

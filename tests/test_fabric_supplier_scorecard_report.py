# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFabricSupplierScorecardReport(TransactionCase):
    """FR-16 (Nhóm 10 - Thống kê tùy nhu cầu) - Thẻ điểm đánh giá Nhà cung cấp.

    Kịch bản: 1 NCC có 2 PO đã xác nhận, 1 cây vải PASS QC + 1 cây vải FAIL
    QC (tỷ lệ PASS = 50%, dưới ngưỡng cảnh báo 90%), và 1 yêu cầu Đổi trả
    loại "Vải lỗi" (tỷ lệ đổi trả = 1/2 PO = 50%, vượt ngưỡng cảnh báo 10%)
    -> báo cáo phải tổng hợp đúng số liệu và bật cờ cảnh báo rủi ro NCC."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'NCC Test Scorecard'})
        cls.fabric_product = cls.env['product.product'].create({
            'name': 'Vải Kaki Scorecard',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
        })

        cls.po_1 = cls.env['purchase.order'].create({
            'partner_id': cls.vendor.id,
            'order_line': [(0, 0, {
                'product_id': cls.fabric_product.id,
                'product_qty': 100.0,
                'product_uom': cls.fabric_product.uom_id.id,
                'price_unit': 10.0,
                'name': cls.fabric_product.name,
            })],
        })
        cls.po_1.button_confirm()

        cls.po_2 = cls.env['purchase.order'].create({
            'partner_id': cls.vendor.id,
            'order_line': [(0, 0, {
                'product_id': cls.fabric_product.id,
                'product_qty': 50.0,
                'product_uom': cls.fabric_product.uom_id.id,
                'price_unit': 10.0,
                'name': cls.fabric_product.name,
            })],
        })
        cls.po_2.button_confirm()

        cls.env['stock.lot'].create({
            'name': 'ROLL-SCORECARD-PASS',
            'product_id': cls.fabric_product.id,
            'company_id': cls.env.company.id,
            'fabric_supplier_id': cls.vendor.id,
            'qc_state': 'pass',
        })
        cls.env['stock.lot'].create({
            'name': 'ROLL-SCORECARD-FAIL',
            'product_id': cls.fabric_product.id,
            'company_id': cls.env.company.id,
            'fabric_supplier_id': cls.vendor.id,
            'qc_state': 'fail',
        })

        cls.env['fabric.return.request'].create({
            'request_type': 'defect',
            'partner_id': cls.vendor.id,
            'purchase_order_id': cls.po_1.id,
            'product_id': cls.fabric_product.id,
            'quantity': 20.0,
        })

    def test_supplier_scorecard_computes_expected_values(self):
        report = self.env['fabric.supplier.scorecard.report'].search([
            ('partner_id', '=', self.vendor.id),
        ])
        self.assertEqual(len(report), 1, 'Phải gộp về đúng 1 dòng cho NCC này.')
        line = report[0]

        self.assertEqual(line.po_count, 2)
        self.assertAlmostEqual(line.po_amount_total, 1500.0, places=2)
        self.assertEqual(line.lot_count, 2)
        self.assertEqual(line.qc_pass_count, 1)
        self.assertEqual(line.qc_fail_count, 1)
        self.assertAlmostEqual(line.qc_pass_rate, 50.0, places=2)
        self.assertEqual(line.defect_return_count, 1)
        self.assertAlmostEqual(line.defect_return_rate, 50.0, places=2)
        self.assertTrue(
            line.supplier_risk_alert,
            'Tỷ lệ PASS QC 50%% < 90%% và tỷ lệ đổi trả 50%% > 10%% -> phải cảnh báo.')

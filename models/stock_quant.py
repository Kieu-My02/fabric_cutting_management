from odoo import models, api, _
from odoo.exceptions import ValidationError


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.constrains('lot_id', 'product_id', 'quantity', 'location_id')
    def _check_fabric_lot_required(self):
        """
        Chan khong cho ton tai quant fabric (tracking='lot') ma thieu lot_id
        tai vi tri noi bo, tranh loi mat truy vet giong quant #26 (22/07/2026).
        """
        for quant in self:
            if (
                quant.location_id.usage == 'internal'
                and quant.product_id.tracking == 'lot'
                and quant.quantity > 0
                and not quant.lot_id
            ):
                raise ValidationError(_(
                    "San pham '%s' bat buoc theo doi theo Lot. "
                    "Khong the co ton kho %.2f %s tai '%s' ma khong co Lot. "
                    "Vui long chon Lot khi dieu chinh ton kho."
                ) % (
                    quant.product_id.display_name,
                    quant.quantity,
                    quant.product_uom_id.name,
                    quant.location_id.complete_name,
                ))
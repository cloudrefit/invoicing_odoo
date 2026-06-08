import base64
import io
import logging

from odoo import models, api
from odoo.tools.pdf import PdfFileReader, PdfFileWriter

_logger = logging.getLogger(__name__)


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        # Call the original method to get the PDF
        pdf_content, report_type = super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        
        # Only process invoices
        if report_ref != 'account.report_invoice_with_payments' and report_ref != 'account.report_invoice':
            return pdf_content, report_type
        if not res_ids:
            return pdf_content, report_type

        # We can only easily embed if we have a single invoice, or we process them one by one
        # Odoo usually renders them together.
        # For simplicity and robustness, if there's only one res_id, we embed.
        if len(res_ids) != 1:
            _logger.warning(
                "action=%s report=%s res_count=%s batch_xml_not_supported",
                'render_pdf', report_ref, len(res_ids)
            )
            return pdf_content, report_type

        invoice = self.env['account.move'].browse(res_ids[0])
        
        # Check context for sandbox xml first, otherwise use invoice field
        xml_string = self.env.context.get('sandbox_signed_xml') or invoice.zatca_signed_xml
        if not xml_string:
            return pdf_content, report_type

        try:
            # Load the PDF
            reader = PdfFileReader(io.BytesIO(pdf_content), strict=False)
            writer = PdfFileWriter()
            writer.cloneReaderDocumentRoot(reader)

            # Prepare the XML attachment
            xml_filename = f"zatca_invoice_{invoice.name.replace('/', '_')}.xml"
            xml_data = xml_string.encode('utf-8')

            # Add the attachment (invisible/embedded)
            writer.add_attachment(xml_filename, xml_data)

            # Output the new PDF
            output = io.BytesIO()
            writer.write(output)
            pdf_content = output.getvalue()
            
            _logger.info("Successfully embedded ZATCA XML into PDF for invoice %s", invoice.name)
        except (ImportError, IOError, ValueError) as e:
            _logger.error("action=%s invoice_id=%s invoice=%s error=%s", 'embed_pdf_xml', invoice.id, invoice.name, str(e))

        return pdf_content, report_type

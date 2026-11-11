import base64
import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas

from pdf_links import page_links


class PDFLinksTests(unittest.TestCase):
    def source(self):
        stream = io.BytesIO()
        c = Canvas(stream, pagesize=(600, 800))
        c.linkRect('', 'target', (60, 640, 240, 680), relative=0)
        c.linkURL('https://example.com/lesson', (60, 560, 240, 600), relative=0)
        c.linkURL('javascript:alert(1)', (60, 500, 240, 540), relative=0)
        c.showPage()
        c.bookmarkPage('target')
        c.drawString(60, 700, 'Destination')
        c.showPage()
        c.save()
        return PdfReader(io.BytesIO(stream.getvalue()))

    def test_destination_and_safe_url(self):
        links = page_links(self.source(), 0)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]['page'], 1)
        for actual, expected in zip(links[0]['rect'], [.1,.15,.3,.05]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(links[1]['url'], 'https://example.com/lesson')

    def test_import_resolves_links_to_imported_frames(self):
        from board_storage import BoardStorage
        from media import Media
        writer = PdfWriter()
        writer.clone_document_from_reader(self.source())
        raw = io.BytesIO()
        writer.write(raw)
        with tempfile.TemporaryDirectory() as folder:
            storage = BoardStorage(Path(folder))
            storage.metadata()
            media = Media(storage)
            job = media.prepare('links.pdf', base64.b64encode(raw.getvalue()).decode())
            for page in range(job['pages']):
                media.render(job['id'], page)
            saved = media.commit(job['id'], 'default', storage.revision('default'))
            records = storage.read('default')['store']
            picture = records['shape:image:' + job['id'] + '-0']
            self.assertEqual(picture['meta']['pdfLinks'][0]['frame'], saved['pages'][1])
            self.assertEqual(len(picture['meta']['pdfLinks']), 2)

    def test_rotated_crop(self):
        reader = self.source()
        reader.pages[0].cropbox = RectangleObject([60,80,540,720])
        reader.pages[0].rotate(90)
        link = page_links(reader,0)[0]
        for actual, expected in zip(link['rect'], [.875,0,.0625,.375]):
            self.assertAlmostEqual(actual,expected)

if __name__ == '__main__':
    unittest.main()

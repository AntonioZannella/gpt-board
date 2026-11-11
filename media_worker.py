"""Short-lived native-decoder process. Never runs inside the HTTP server."""

import io
import json
import math
import sys
from pathlib import Path


def image_page(source):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 24_000_000
    with Image.open(source) as image:
        if (
            image.format not in ("PNG", "JPEG")
            or image.width * image.height > 24_000_000
        ):
            raise ValueError(
                "Only PNG/JPEG up to 24 million decoded pixels are supported."
            )
        image.load()
        image.thumbnail((3000, 3000))
        return image.convert("RGB")


def run(action, source, output, page=0):
    if action in ("pdf", "pdf-files"):
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen.canvas import Canvas

        from board_storage import decode_png

        pages = json.loads(source.read_text())["pages"]
        if not isinstance(pages, list) or not 1 <= len(pages) <= 500:
            raise ValueError("Export supports 1–500 pages.")
        canvas = Canvas(str(output), pageCompression=1)
        for item in pages:
            w, h = (
                item["w"] * 0.612,
                item["h"] * 0.612,
            )  # 1000 board units = US Letter width
            if not all(
                isinstance(n, (int, float)) and math.isfinite(n) and 1 <= n <= 14400
                for n in (w, h)
            ):
                raise ValueError("Invalid PDF page dimensions.")
            raw = Path(item["file"]).read_bytes() if action == "pdf-files" else decode_png(item["png"])
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                canvas.setPageSize((w, h))
                canvas.drawImage(ImageReader(image), 0, 0, w, h)
                canvas.showPage()
        canvas.save()
        return {"pages": len(pages)}
    if source.suffix == ".pdf":
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(source)) as document:
            count = len(document)
            if not 1 <= count <= 500:
                raise ValueError("PDF import supports 1–500 pages.")
            if action == "inspect":
                return {"pages": count}
            if not 0 <= page < count:
                raise ValueError("Invalid PDF page number.")
            pdf_page = document[page]
            try:
                w, h = pdf_page.get_size()
                if not all(math.isfinite(n) and 0 < n <= 14400 for n in (w, h)):
                    raise ValueError("Unsupported PDF page dimensions.")
                bitmap = pdf_page.render(scale=min(2.5, 3000 / max(w, h)))
                try:
                    image = bitmap.to_pil().convert("RGB")
                    image.save(output, "PNG")
                    from pdf_links import read_page_links
                    return {"width": image.width, "height": image.height,
                            "links": read_page_links(source, page)}
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
    image = image_page(source)
    if action == "inspect":
        return {"pages": 1}
    image.save(output, "PNG")
    return {"width": image.width, "height": image.height}


if __name__ == "__main__":
    try:
        if sys.platform != "win32":
            import resource

            limit = 110 if sys.argv[1] == "pdf-files" else 25
            resource.setrlimit(resource.RLIMIT_CPU, (limit, limit))
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
        print(
            json.dumps(
                run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
            )
        )
    except Exception:  # noqa: BLE001 - isolate untrusted native decoder failures
        # Parser paths, embedded document text and passwords never reach logs.
        print(
            json.dumps(
                {
                    "error": "Unsupported, damaged, encrypted or oversized document. Try a smaller PDF or PNG/JPEG image."
                }
            )
        )
        sys.exit(1)

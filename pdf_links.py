"""Extract safe PDF navigation without flattening it into the page bitmap."""
import math
from urllib.parse import urlsplit


def page_links(reader, page_number):
    page = reader.pages[page_number]
    box = page.cropbox
    left, bottom, right, top = map(float, box)
    width, height = right - left, top - bottom
    rotation = page.rotation % 360

    def point(x, y):
        x, y = (x - left) / width, (top - y) / height
        return {0: (x, y), 90: (1-y, x), 180: (1-x, 1-y), 270: (y, 1-x)}[rotation]

    result = []
    for ref in page.get('/Annots', [])[:1000]:
        try:
            annotation = ref.get_object()
            if annotation.get('/Subtype') != '/Link':
                continue
            x0, y0, x1, y1 = map(float, annotation['/Rect'])
            if not all(math.isfinite(n) for n in (x0,y0,x1,y1)):
                continue
            a, b = point(x0, y0), point(x1, y1)
            x, y = max(0, min(a[0], b[0])), max(0, min(a[1], b[1]))
            w, h = min(1, max(a[0], b[0]))-x, min(1, max(a[1], b[1]))-y
            if w <= 0 or h <= 0:
                continue
            link = {'rect': [x, y, w, h]}
            action = annotation.get('/A', {})
            action = action.get_object() if hasattr(action, 'get_object') else action
            if action.get('/S') == '/URI':
                url = str(action.get('/URI', ''))
                if len(url) <= 4096 and urlsplit(url).scheme.lower() in ('http', 'https'):
                    link['url'] = url
            else:
                destination = annotation.get('/Dest')
                if destination is None and action.get('/S') == '/GoTo':
                    destination = action.get('/D')
                if isinstance(destination, str):
                    named = reader.named_destinations.get(destination)
                    target = reader.get_destination_page_number(named) if named else None
                elif destination:
                    first = destination[0]
                    target = (int(first) if isinstance(first, int)
                              else reader.get_page_number(first.get_object()))
                else:
                    target = None
                if isinstance(target, int) and 0 <= target < len(reader.pages):
                    link['page'] = target
            if 'url' in link or 'page' in link:
                result.append(link)
        except (KeyError, ValueError, TypeError, AttributeError, IndexError):
            # A malformed or unsupported annotation must not lose the page.
            continue
    return result


def read_page_links(source, page_number):
    from pypdf import PdfReader
    return page_links(PdfReader(source), page_number)

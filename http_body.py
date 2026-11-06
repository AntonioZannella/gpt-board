"""Bounded HTTP request framing shared by the board and iPad bridge."""
import re

MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = MAX_BODY_BYTES
MAX_LINE_BYTES = 8192


class BodyError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def read_body(stream, headers):
    lengths = headers.get_all('Content-Length', [])
    encodings = headers.get_all('Transfer-Encoding', [])
    if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
        raise BodyError('Ambiguous request framing.')

    def read_exact(size):
        data = stream.read(size)
        if len(data) != size:
            raise BodyError('Incomplete request body.')
        return data

    def line():
        value = stream.readline(MAX_LINE_BYTES + 1)
        if len(value) > MAX_LINE_BYTES or not value.endswith(b'\r\n'):
            raise BodyError('Invalid chunk framing.')
        return value

    if encodings:
        if encodings[0].lower() != 'chunked':
            raise BodyError('Unsupported transfer encoding.')
        chunks, total = [], 0
        while True:
            raw_size = line()[:-2].split(b';', 1)[0]
            if not re.fullmatch(rb'[0-9a-fA-F]+', raw_size):
                raise BodyError('Invalid chunk length.')
            size = int(raw_size, 16)
            if size > MAX_CHUNK_BYTES or total + size > MAX_BODY_BYTES:
                raise BodyError('Request body is too large.', 413)
            if size == 0:
                trailer_bytes = 0
                while True:
                    trailer = line()
                    trailer_bytes += len(trailer)
                    if trailer_bytes > MAX_LINE_BYTES:
                        raise BodyError('Chunk trailers are too large.')
                    if trailer == b'\r\n':
                        return b''.join(chunks)
            chunks.append(read_exact(size))
            total += size
            if read_exact(2) != b'\r\n':
                raise BodyError('Invalid chunk framing.')
    length = lengths[0] if lengths else '0'
    if not re.fullmatch(r'[0-9]+', length):
        raise BodyError('Invalid Content-Length.')
    # Avoid converting arbitrarily long decimal integers.
    length = length.lstrip('0') or '0'
    if len(length) > 10 or int(length) > MAX_BODY_BYTES:
        raise BodyError('Request body is too large.', 413)
    return read_exact(int(length))

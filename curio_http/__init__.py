import logging
from functools import partial
import json
import curio
import cgi
import yarl
import h11

logger = logging.getLogger(__name__)


class HTTPError(Exception):
    def __init__(self, *args, **kwargs):
        self.response = kwargs.pop('response')
        super().__init__(*args, **kwargs)


def get_encoding_from_headers(headers):
    """Returns encodings from given HTTP Header Dict.

    Args:
        headers (dict): dictionary to extract encoding from.

    Returns:
        str
    """
    content_type = headers.get('content-type')

    if not content_type:
        return None

    content_type, params = cgi.parse_header(content_type)

    if 'charset' in params:
        return params['charset'].strip("'\"")

    if 'text' in content_type:
        return 'ISO-8859-1'


class _EventIterator:
    """Receive all remaining data from a connection."""
    def __init__(self, event_source):
        self.event_source = event_source

    async def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.event_source()

        if type(event) is h11.Data:
            return event.data
        elif type(event) is h11.EndOfMessage:
            raise StopAsyncIteration
        else:
            raise ValueError('Unknown h11 event: %r', event)


class Response:
    """Contains the complete result of an async request."""

    def __init__(self, raw_response, h11_request, conn):
        self.status_code = raw_response.status_code
        self.http_version = raw_response.http_version.decode('utf-8')

        self.headers = {
            key.decode('utf-8'): value.decode('utf-8')
            for key, value in raw_response.headers
        }

        self.h11_request = h11_request
        self.conn = conn

        self.history = None

    def __repr__(self):
        return '<Response [%s]>' % (self.status_code)

    @property
    def url(self):
        """The final URL that was requested."""
        return '{host}:{port}{target}'.format(
            host=self.conn.host,
            port=self.conn.port,
            target=self.h11_request.target.decode('utf-8'),
        )

    def raise_for_status(self):
        """Raises HTTPError, if one occurred."""
        http_error_msg = ''

        if 400 <= self.status_code < 500:
            http_error_msg = '%s Client Error for url: %s' % (
                self.status_code, self.url)

        elif 500 <= self.status_code < 600:
            http_error_msg = '%s Server Error for url: %s' % (
                self.status_code, self.url)

        if http_error_msg:
            raise HTTPError(http_error_msg, response=self)

    @property
    def is_redirect(self):
        """Whether the response is a well-formed redirect."""
        return 'location' in self.headers and 301 <= self.status_code < 400

    def iter_chunked(self, maxsize=None):
        """Stream raw response body, maxsize bytes at a time."""
        return _EventIterator(partial(self.conn._next_event, maxsize))

    async def binary(self):
        """Return the full response body as a bytearray."""
        data = None
        async for chunk in self.iter_chunked():
            if data is None:
                data = chunk
            else:
                data += chunk

        return data

    async def text(self):
        """Return the full response body as a string."""
        data = await self.binary()
        if data is None:
            return None

        encoding = get_encoding_from_headers(self.headers)

        return data.decode(encoding)

    async def json(self):
        """Return the full response body as parsed JSON."""
        data = await self.binary()
        if data is None:
            return None

        return json.loads(data.decode('utf-8'))


class HTTPConnection:
    """Maries an async socket with an HTTP handler."""

    def __init__(self, host, port, ssl):
        self.host = host
        self.port = port
        self.ssl = ssl

    def __repr__(self):
        return '%s(host=%r, port=%r)' % (
            self.__class__.__name__, self.host, self.port)

    async def open(self):
        sock_args = dict(
            host=self.host,
            port=self.port,
        )

        if self.ssl:
            sock_args.update(dict(
                ssl=self.ssl,
                server_hostname=self.host
            ))

        self.sock = await curio.open_connection(**sock_args)
        self.state = h11.Connection(our_role=h11.CLIENT)

        logger.debug('Opened %r', self)

    async def close(self):
        await self.sock.close()

        self.sock = None
        self.state = None

        logger.debug('Closed %r', self)

    async def _send(self, event):
        # logger.debug("Sending event: %s", event)

        data = self.state.send(event)
        await self.sock.sendall(data)

    async def _next_event(self, maxsize=None):
        if not maxsize:
            maxsize = 2048

        while True:
            event = self.state.next_event()

            # logger.debug("Received event: %s", event)

            if event is h11.NEED_DATA:
                data = await self.sock.recv(maxsize)
                self.state.receive_data(data)
                continue

            return event

    async def request(self, h11_request, data=None):
        await self._send(h11_request)

        if data is not None:
            await self._send(h11.Data(data=data))

        await self._send(h11.EndOfMessage())

        event = await self._next_event()

        assert type(event) is h11.Response

        return event


def _prepare_request(method, url, params=None, headers=None, data=None):
    """
    Args:
        method (str): The HTTP method.
        url (URL): A YARL URL object.
        headers (dict): A dictionary with HTTP headers to send.
        data (str): Data to send in the request body.
    """
    if params:
        query_vars = list(url.query.items()) + list(params.items())
        url = url.with_query(query_vars)

    target = str(url.relative())

    if headers is None:
        headers = {}

    headers.setdefault('Host', url.raw_host)

    if data is not None and 'Transfer-Encoding' not in headers:
        headers['Content-Length'] = str(len(data)).encode('utf-8')

    h11_request = h11.Request(
        method=method,
        target=target,
        headers=list(headers.items())
    )

    if data is not None:
        body = data.encode('utf-8')
    else:
        body = None

    return h11_request, body


class ClientSession:

    def __init__(self):
        self.open_connections = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for conn in self.open_connections:
            await conn.close()

    async def _request(self, method, url, *args, **kwargs):
        h11_request, body = _prepare_request(
            method, url, *args, **kwargs)

        conn = HTTPConnection(
            host=url.raw_host,
            port=url.port,
            ssl=url.scheme == 'https',
        )

        await conn.open()

        self.open_connections.append(conn)

        raw_response = await conn.request(h11_request, body)

        return Response(raw_response, h11_request, conn)

    async def request(
            self, method, url, *args, allow_redirects=False, **kwargs):
        """Perform HTTP request."""
        url = yarl.URL(url)

        response = await self._request(method, url, *args, **kwargs)

        if allow_redirects:
            history = []

            while response.is_redirect:
                history.append(response)

                # Redirects can be relative.
                new_url = url.join(yarl.URL(response.headers['location']))

                response = await self._request(method, new_url)

            response.history = history

        return response

    def get(self, *args, allow_redirects=True, **kwargs):
        """Perform HTTP GET request."""
        return self.request(
            'GET', *args, allow_redirects=allow_redirects, **kwargs)

    def post(self, *args, data=None, **kwargs):
        """Perform HTTP POST request."""
        return self.request('POST', *args, data=data, **kwargs)

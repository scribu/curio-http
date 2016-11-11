import pytest
import curio
import curio_http


@pytest.fixture
def kernel():
    kernel = curio.Kernel(log_errors=False)

    def handle_crash(current):
        raise current.exc_info[1]

    kernel._crash_handler = handle_crash

    return kernel


async def request(*args, out=None, **kwargs):
    async with curio_http.ClientSession() as session:
        response = await session.request(*args, **kwargs)
        if out:
            data = await getattr(response, out)()
            return response, data

    return response


def test_params(kernel):
    response = kernel.run(request(
        'GET', 'http://example.com/path?set=via_url'))

    assert response.url == 'example.com:80/path?set=via_url'

    response = kernel.run(request(
        'GET', 'http://example.com/path?set=via_url',
        params={'set': 'via_params'}))

    assert response.url == 'example.com:80/path?set=via_url&set=via_params'


def test_get_404(kernel):
    response, text = kernel.run(request(
        'GET', 'http://httpbin.org/status/404', out='text'))

    assert response.http_version == '1.1'
    assert response.status_code == 404

    assert response.headers['content-type'] == 'text/html; charset=utf-8'

    with pytest.raises(curio_http.HTTPError):
        response.raise_for_status()

    assert text is None


def test_get_binary(kernel):
    response, binary = kernel.run(request(
        'GET', 'https://httpbin.org/bytes/10000', out='binary'))

    # Should do nothing.
    response.raise_for_status()

    assert len(binary) == 10000


def test_get_json(kernel):
    response, json_content = kernel.run(request(
        'GET', 'http://httpbin.org/get', out='json'))

    assert response.http_version == '1.1'
    assert response.status_code == 200

    assert response.headers['content-type'] == 'application/json'

    assert json_content['headers'] == {
        'Host': 'httpbin.org'
    }


def test_post(kernel):
    response, json = kernel.run(request(
        'POST', 'http://httpbin.org/post', data='foo=bar', out='json'))

    assert response.status_code == 200

    assert json['data'] == 'foo=bar'


def test_ssl(kernel):
    response, json = kernel.run(request(
        'GET', 'https://httpbin.org/get', out='json'))

    assert response.http_version == '1.1'
    assert response.status_code == 200

    assert response.headers['content-type'] == 'application/json'

    assert json['headers'] == {
        'Host': 'httpbin.org'
    }


@pytest.mark.parametrize('redirect_type', ['absolute', 'relative'])
def test_redirect(kernel, redirect_type):
    url = 'http://httpbin.org/{}-redirect/2'.format(redirect_type)

    response = kernel.run(request('GET', url, allow_redirects=False))
    assert response.is_redirect
    assert response.url == 'httpbin.org:80/{}-redirect/2'.format(redirect_type)

    response = kernel.run(request('GET', url, allow_redirects=True))
    assert not response.is_redirect
    assert response.url == 'httpbin.org:80/get'
    assert [r.url for r in response.history] == [
        'httpbin.org:80/{}-redirect/2'.format(redirect_type),
        'httpbin.org:80/{}-redirect/1'.format(redirect_type),
    ]

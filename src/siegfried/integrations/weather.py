"""Fixed Lima HTTPS destination, bounded output and no proxies/redirects."""
import http.client
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.request

from siegfried.core.briefing import screen_text

WEATHER_URL = 'https://wttr.in/Lima?format=%c+%t+%C'
MAX_BYTES = 512
TIMEOUT = .8


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('weather_redirect_rejected')


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self.host != 'wttr.in' or self.port != 443 or self._tunnel_host:
            raise ValueError('weather_destination_rejected')
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('weather_address_rejected')
        family, socktype, proto, _, address = addresses[0]
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(TIMEOUT)
            sock.connect(address)  # Pin the validated address; no second DNS resolution.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=ssl.create_default_context())


def parse_weather(raw, content_type='text/plain; charset=utf-8'):
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES or
            not isinstance(content_type, str) or content_type.split(';')[0].strip().lower() != 'text/plain' or
            ('charset=' in content_type.lower() and not re.search(r'charset\s*=\s*"?utf-8"?(?:\s*;|\s*$)',content_type,re.I))):
        return None
    try:
        text = raw.decode('utf-8').strip()
    except UnicodeError:
        return None
    clean = screen_text(text, 120)
    if not clean or not re.search(r'[+-]?\d{1,2}°C\b', clean):
        return None
    return clean


def fetch_weather(opener=None):
    try:
        opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                      PublicHTTPSHandler())
        request = urllib.request.Request(WEATHER_URL, headers={'User-Agent':'Siegfried/1.0', 'Accept':'text/plain'})
        with opener.open(request, timeout=TIMEOUT) as response:
            if response.status != 200 or response.geturl() != WEATHER_URL:
                return None
            return parse_weather(response.read(MAX_BYTES + 1), response.headers.get('Content-Type', ''))
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        return None

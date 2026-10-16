"""Read-only HTTP smoke checks. Run with Python 3; no WordPress writes."""
from html.parser import HTMLParser
import urllib.parse
import urllib.request

BASE = 'http://localhost:18080/'

class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = 0
        self.images = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article' and 'product-card' in attrs.get('class', '').split():
            self.cards += 1
        if tag == 'img' and attrs.get('src'):
            self.images.append(attrs['src'])
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])

def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        assert response.status == 200, (url, response.status)
        body = response.read().decode('utf-8')
    assert 'Fatal error' not in body and '<b>Warning</b>' not in body, url
    page = Page()
    page.feed(body)
    return page, body

for suffix, count, marker in [
    ('', 4, 'Dịu dàng như sen.'),
    ('?post_type=bg_product', 10, '10 sản phẩm'),
    ('?bg_product_category=cham-soc-da', 6, 'Chăm sóc da'),
    ('?bg_product_category=trang-diem', 3, 'Trang điểm'),
    ('?bg_product_category=chong-nang', 1, 'Chống nắng'),
    ('?s=Dior&post_type=bg_product', 1, 'Dior Addict Lip Glow'),
    ('?s=zzzznonexistent&post_type=bg_product', 0, 'Chưa tìm thấy'),
    ('?bg_page=gioi-thieu', 0, 'Chào bạn'),
    ('?bg_page=lien-he', 0, 'Gửi góp ý trên GitHub'),
]:
    page, body = fetch(BASE + suffix)
    assert page.cards == count and marker in body, (suffix, page.cards, marker)
    print('PASS HTTP 200', suffix or '/', 'cards=', page.cards)

for i in range(1, 11):
    page, body = fetch(BASE + '?bg_product=bg%03d' % i)
    assert 'product-detail' in body and f'BG{i:03}' in body
    assert ('detail-photo' in body)
    for image in page.images:
        parsed = urllib.parse.urlparse(image)
        if parsed.hostname in ('localhost', '127.0.0.1'):
            encoded = urllib.parse.quote(image, safe=':/?&=%#')
            with urllib.request.urlopen(urllib.request.Request(encoded, method='HEAD'), timeout=20) as r:
                assert r.status == 200
    print('PASS detail BG%03d; images=%s' % (i, len(page.images)))

with urllib.request.urlopen('http://localhost:18081/', timeout=20) as response:
    assert response.status == 200
print('PASS phpMyAdmin HTTP 200')

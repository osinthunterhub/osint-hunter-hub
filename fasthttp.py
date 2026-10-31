"""
⚡ fasthttp — bot-wide HTTP engine (built by HIMANSHU)
- 1 shared connection-pool (TLS-reuse → har API call ~100-300ms tez)
- default-timeout inject (koi call hang NAHI kar sakta)
- auto-retry (connection err / 502-504) with backoff
Drop-in: 'import requests as _rq'  →  'import fasthttp as _rq'
"""
import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except Exception:  # purane urllib3
    from requests.packages.urllib3.util.retry import Retry


class FastSession(requests.Session):
    def __init__(self, default_timeout: float = 25.0):
        super().__init__()
        self._dtimeout = default_timeout
        try:
            retry = Retry(total=2, backoff_factor=0.4, status_forcelist=(502, 503, 504),
                          allowed_methods=frozenset(["GET", "POST"]))
        except TypeError:
            retry = Retry(total=2, backoff_factor=0.4, status_forcelist=(502, 503, 504))
        ad = HTTPAdapter(pool_connections=20, pool_maxsize=50, max_retries=retry)
        self.mount("https://", ad)
        self.mount("http://", ad)

    def request(self, method, url, **kw):
        kw.setdefault("timeout", self._dtimeout)
        return super().request(method, url, **kw)


S = FastSession()
request = S.request   # v2.24.9 CRITICAL: vault.py requests.request(...) use karta tha — expose nahi tha = SAARE vault-backups day-1 se fail!
get = S.get
post = S.post
put = S.put
delete = S.delete
head = S.head

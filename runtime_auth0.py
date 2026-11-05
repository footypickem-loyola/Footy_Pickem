"""Bounded Auth0 transport/ID-token validation adapted from tested private-beta code.
No bootstrap, account data, onboarding, startup network calls or token logging.
"""
from dataclasses import dataclass, field
from pathlib import Path
import json, re, threading, time, hmac
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError
import jwt
from runtime_auth_sessions import AuthRefused, need

@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    client_id: str
    authorization_endpoint: str
    redirect_uri: str

    @property
    def origin(self):
        u=urlsplit(self.redirect_uri); return u.scheme+'://'+u.netloc

    def validate(self):
        for url in (self.issuer,self.authorization_endpoint,self.redirect_uri):
            u=urlsplit(url); need(u.scheme=='https' and u.hostname and not u.username and not u.password and not u.fragment and not u.query)
        need(bool(self.client_id) and urlsplit(self.redirect_uri).path=='/auth/callback')
        need(urlsplit(self.authorization_endpoint).netloc==urlsplit(self.issuer).netloc)

def verify_id_token(token,settings,provider,nonce):
    try:
        need(isinstance(token,str) and len(token)<=16384)
        header=jwt.get_unverified_header(token)
        need(header.get('alg')=='RS256' and isinstance(header.get('kid'),str) and 0<len(header['kid'])<=256)
        need(not any(k in header for k in ('jku','jwk','x5u','crit')))
        # Trusted pinned key resolver, never a jku/jwk URL supplied by the token.
        key=provider.signing_key(header['kid'])
        claims=jwt.decode(token,key,algorithms=['RS256'],issuer=settings.issuer,audience=settings.client_id,
            options={'require':['iss','sub','aud','exp','iat','nonce'],'strict_aud':True},leeway=0)
        need(isinstance(claims['sub'],str) and 0<len(claims['sub'])<=512)
        need(isinstance(claims['nonce'],str) and hmac.compare_digest(claims['nonce'],nonce))
        need(claims.get('azp',settings.client_id)==settings.client_id)
        # Email is neither requested nor used for account association.
        return claims['iss'],claims['sub']
    except Exception:
        raise AuthRefused('AUTHENTICATION_REFUSED') from None

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, *, form=None):
    """TLS verified by urllib. No proxies, redirects, retries or response logging."""
    try:
        need(urlsplit(url).scheme == 'https')
        data = urlencode(form).encode('ascii') if form is not None else None
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        req = Request(url, data=data, headers=headers)
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=5) as response:
            need(response.status == 200)
            raw = response.read(262145)
            need(len(raw) <= 262144)
            value = json.loads(raw)
            need(isinstance(value, dict))
            return value
    except Exception as exc:
        if isinstance(exc, HTTPError):
            exc.close()
        raise AuthRefused('AUTHENTICATION_REFUSED') from None


@dataclass(frozen=True)
class Auth0Config:
    enabled: bool = False
    issuer: str = ''
    client_id: str = ''
    client_secret: str = field(default='', repr=False)
    callback_url: str = ''
    session_path: str = ''

    @classmethod
    def from_mapping(cls, values):
        # Reading configuration is explicit. Environment alone registers nothing.
        flag = values.get('RUNTIME_AUTH0_ENABLED', '0')
        need(flag in ('0', '1'))
        if flag == '0':
            return cls()
        cfg = cls(True, values.get('RUNTIME_AUTH0_ISSUER', ''),
                  values.get('RUNTIME_AUTH0_CLIENT_ID', ''),
                  values.get('RUNTIME_AUTH0_CLIENT_SECRET', ''),
                  values.get('RUNTIME_AUTH0_CALLBACK_URL', ''),
                  values.get('RUNTIME_AUTH0_SESSION_DB', ''))
        cfg.validate()
        return cfg

    @property
    def settings(self):
        return OIDCSettings(self.issuer, self.client_id, self.issuer+'authorize', self.callback_url)

    def validate(self):
        need(type(self.enabled) is bool)
        need(all(isinstance(v, str) for v in (self.issuer, self.client_id, self.client_secret,
                                              self.callback_url, self.session_path)))
        self.settings.validate()
        issuer = urlsplit(self.issuer)
        need(issuer.path == '/' and issuer.port is None and issuer.hostname == issuer.netloc
             and '.' in issuer.hostname and '%' not in self.issuer and '\\' not in self.issuer)
        need(1 <= len(self.client_id) <= 256 and self.client_id.isascii()
             and not any(c.isspace() for c in self.client_id))
        need(16 <= len(self.client_secret) <= 4096 and not any(c.isspace() for c in self.client_secret))
        callback = urlsplit(self.callback_url)
        need(callback.port is None and callback.hostname == callback.netloc
             and '%' not in self.callback_url and '\\' not in self.callback_url)
        path = Path(self.session_path)
        need(path.is_absolute() and path.is_file())


class Auth0Keys(jwt.PyJWKClient):
    """Use PyJWT's expiring key-set cache and unknown-kid cooldown, not an LRU."""
    def fetch_data(self):
        data = request_json(self.uri)
        keys = data.get('keys')
        need(isinstance(keys, list) and 1 <= len(keys) <= 32)
        ids = []
        for key in keys:
            need(isinstance(key, dict) and key.get('kty') == 'RSA'
                 and key.get('use', 'sig') == 'sig' and key.get('alg', 'RS256') == 'RS256'
                 and isinstance(key.get('kid'), str) and 0 < len(key['kid']) <= 256)
            need(not any(k in key for k in ('d', 'p', 'q')))
            ids.append(key['kid'])
        need(len(ids) == len(set(ids)))
        parsed = jwt.PyJWKSet.from_dict(data)
        need(len(parsed.keys) == len(keys) and all(key.key.key_size >= 2048 for key in parsed.keys))
        # PyJWT supports fetch_data overrides; preserve its cooldown bookkeeping.
        self._last_successful_fetch = time.monotonic()
        return data


class Auth0Provider:
    testing_only = False

    def __init__(self, config):
        config.validate()
        self.config = config
        self.keys = Auth0Keys(config.issuer+'.well-known/jwks.json', cache_keys=False,
                              lifespan=300, cooldown_duration=30, timeout=5)
        self.metadata_until = 0
        self.response_iss_supported = False
        self.lock = threading.RLock()

    def metadata(self):
        with self.lock:
            if time.monotonic() < self.metadata_until:
                return
            c = self.config
            value = request_json(c.issuer+'.well-known/openid-configuration')
            need(all(value.get(k) == v for k, v in {
                'issuer': c.issuer, 'authorization_endpoint': c.issuer+'authorize',
                'token_endpoint': c.issuer+'oauth/token',
                'jwks_uri': c.issuer+'.well-known/jwks.json'}.items()))
            need('RS256' in value.get('id_token_signing_alg_values_supported', [])
                 and 'code' in value.get('response_types_supported', [])
                 and 'S256' in value.get('code_challenge_methods_supported', [])
                 and 'client_secret_post' in value.get('token_endpoint_auth_methods_supported', []))
            supported=value.get('authorization_response_iss_parameter_supported',False)
            need(type(supported) is bool)
            # Never downgrade an advertised requirement during this process.
            self.response_iss_supported = self.response_iss_supported or supported
            self.metadata_until = time.monotonic()+300

    def validate_response_issuer(self, issuer):
        self.metadata()
        need(issuer == self.config.issuer if self.response_iss_supported
             else issuer is None or issuer == self.config.issuer)

    def exchange(self, *, code, code_verifier, redirect_uri):
        try:
            need(isinstance(code, str) and code.isascii() and 0 < len(code) <= 2048)
            need(isinstance(code_verifier, str) and re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', code_verifier))
            need(redirect_uri == self.config.callback_url)
            self.metadata()
            value = request_json(self.config.issuer+'oauth/token', form={
                'grant_type': 'authorization_code', 'client_id': self.config.client_id,
                'client_secret': self.config.client_secret, 'code': code,
                'code_verifier': code_verifier, 'redirect_uri': redirect_uri})
            token = value.get('id_token')
            need(isinstance(token, str) and 0 < len(token) <= 16384)
            # No access/refresh tokens retained or returned to the browser.
            return token
        except Exception:
            raise AuthRefused('AUTHENTICATION_REFUSED') from None

    def signing_key(self, kid):
        try:
            need(isinstance(kid, str) and 0 < len(kid) <= 256)
            self.metadata()
            key = self.keys.get_signing_key(kid)
            need(key.algorithm_name == 'RS256')
            return key
        except Exception:
            raise AuthRefused('AUTHENTICATION_REFUSED') from None

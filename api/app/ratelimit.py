from slowapi import Limiter

from app.core.ratelimit_key import trusted_client_ip

# headers_enabled stays False: enabling it breaks endpoints that return
# Pydantic objects (slowapi's auto-injector requires a starlette Response).
# Retry-After and the X-RateLimit-* headers are emitted explicitly by the
# 429 handler in app.main._rate_limit_handler.
limiter = Limiter(key_func=trusted_client_ip)

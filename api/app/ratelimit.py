from slowapi import Limiter

from app.core.ratelimit_key import trusted_client_ip

limiter = Limiter(key_func=trusted_client_ip)

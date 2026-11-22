from ipaddress import ip_address, ip_network

from django.conf import settings
from django.http import HttpResponseForbidden


class RedeAdministrativaMiddleware:
    """O microserviço não confia em cabeçalhos de proxy enviados pelo cliente."""
    def __init__(self, get_response):
        self.get_response = get_response
        self.redes = [ip_network(r.strip(), strict=False) for r in settings.ADMIN_ALLOWED_NETWORKS if r.strip()]

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if 'admin' not in request.resolver_match.app_names:
            return None
        try:
            ip = ip_address(request.META.get('REMOTE_ADDR', ''))
            if any(ip in rede for rede in self.redes):
                return None
        except ValueError:
            pass
        return HttpResponseForbidden('Administração restrita à rede autorizada.')

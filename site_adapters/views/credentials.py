"""
Credential management endpoints.
"""
import os
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.credentials import (
    delete_shared_basic_auth,
    delete_shared_cookie,
    delete_shared_header,
    delete_shared_token,
    get_auth_requirements_for_domain_key,
    list_shared_credentials,
    save_shared_basic_auth,
    save_shared_cookie,
    save_shared_header,
    save_shared_token,
)
from site_adapters.views.helpers import _get_base_dir

VALID_SCOPES = ('', 'metadata', 'snapshot', 'reader', 'health')


def _validate_scope(scope: str) -> str:
    """Validate scope, returning '' for None/empty."""
    if not scope:
        return ''
    if scope not in VALID_SCOPES:
        return '__invalid__'
    return scope


def _get_domains_needing_auth(base_dir):
    """Return list of domains with their auth requirements (section-aware)."""
    domains = []
    if not base_dir or not os.path.isdir(base_dir):
        return domains
    from site_adapters.services.config.loader import _cache
    all_config = _cache.load(base_dir)
    for key in sorted(k for k in all_config if k != 'defaults' and not k.startswith('_')):
        req = get_auth_requirements_for_domain_key(key, base_dir=base_dir)
        domain_auth = req.get('domain', {})
        sections = req.get('sections', {})

        # Check if domain or any section needs auth
        has_any = domain_auth.get('cookie') or domain_auth.get('headers_active') or \
                  domain_auth.get('oauth2') or domain_auth.get('basic_auth')
        for sec in ('metadata', 'snapshot', 'reader', 'health'):
            sec_auth = sections.get(sec, {})
            if sec_auth.get('cookie') or sec_auth.get('headers_active') or \
               sec_auth.get('oauth2') or sec_auth.get('basic_auth'):
                has_any = True

        if has_any:
            domains.append({
                'domain': key,
                'domain_auth': domain_auth,
                'sections': sections,
            })
    return domains


@login_required
@require_http_methods(["GET"])
def user_credentials(request):
    """Return domains needing auth as JSON (for add-credential modal)."""
    base_dir = _get_base_dir()
    domains = _get_domains_needing_auth(base_dir)
    return JsonResponse({'domains': domains})


# ── Shared credential management views ──

@login_required
@require_http_methods(["GET"])
def shared_credential_list(request):
    """List all shared credentials. Returns JSON for the admin page credentials tab."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Permission denied'}, status=403)
    base_dir = _get_base_dir()
    domains = _get_domains_needing_auth(base_dir)
    credentials = list_shared_credentials(include_values=True)
    return JsonResponse({
        'credentials': credentials,
        'domains': domains,
    })


@login_required
@require_http_methods(["POST"])
def shared_credential_save(request):
    """Save a shared credential. Requires staff/superuser access."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    domain = request.POST.get('domain', '').strip()
    cred_type = request.POST.get('type', 'cookie')
    scope = _validate_scope(request.POST.get('scope', '').strip())

    if scope == '__invalid__':
        return JsonResponse({'error': 'Invalid scope'}, status=400)
    if not domain or not is_safe_domain_key(domain):
        return JsonResponse({'error': 'Invalid domain'}, status=400)

    if cred_type == 'cookie':
        cookie_str = request.POST.get('value', '').strip()
        if not cookie_str:
            return JsonResponse({'error': 'Cookie value required'}, status=400)
        save_shared_cookie(domain=domain, cookie_str=cookie_str, scope=scope)
    elif cred_type == 'header':
        header_name = request.POST.get('header_name', '').strip()
        header_value = request.POST.get('value', '').strip()
        if not header_name or not header_value:
            return JsonResponse({'error': 'Header name and value required'}, status=400)
        save_shared_header(domain=domain, header_name=header_name, value=header_value, scope=scope)
    elif cred_type == 'oauth2':
        token_value = request.POST.get('value', '').strip()
        if not token_value:
            return JsonResponse({'error': 'Token value required'}, status=400)
        save_shared_token(domain=domain, refresh_token=token_value, scope=scope)
    elif cred_type == 'basic_auth':
        username_val = request.POST.get('username', '').strip()
        password_val = request.POST.get('password', '').strip()
        if not username_val or not password_val:
            return JsonResponse({'error': 'Username and password required'}, status=400)
        save_shared_basic_auth(domain=domain, username_val=username_val,
                               password_val=password_val, scope=scope)
    else:
        return JsonResponse({'error': 'Invalid credential type'}, status=400)

    return JsonResponse({'success': True})


@login_required
@require_http_methods(["POST"])
def shared_credential_delete(request):
    """Delete a shared credential. Requires staff/superuser access."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    domain = request.POST.get('domain', '').strip()
    cred_type = request.POST.get('type', 'cookie')
    scope = _validate_scope(request.POST.get('scope', '').strip())

    if scope == '__invalid__':
        return JsonResponse({'error': 'Invalid scope'}, status=400)
    if not domain or not is_safe_domain_key(domain):
        return JsonResponse({'error': 'Invalid domain'}, status=400)

    if cred_type == 'cookie':
        delete_shared_cookie(domain=domain, scope=scope)
    elif cred_type == 'header':
        header_name = request.POST.get('header_name', '').strip()
        if not header_name:
            return JsonResponse({'error': 'Header name required'}, status=400)
        delete_shared_header(domain=domain, header_name=header_name, scope=scope)
    elif cred_type == 'oauth2':
        delete_shared_token(domain=domain, scope=scope)
    elif cred_type == 'basic_auth':
        delete_shared_basic_auth(domain=domain, scope=scope)
    else:
        return JsonResponse({'error': 'Invalid credential type'}, status=400)

    return JsonResponse({'success': True})


# ── Domain matching endpoint ──

@login_required
@require_http_methods(["GET"])
def match_domain_config(request):
    """Backend domain matching for the credential modal.

    Uses the same match_domain logic as load_domain_config to handle
    exact domains, wildcards, subdomains, and aliases.
    Returns matched config details or {'matched': false}.
    """
    domain_input = request.GET.get('domain', '').strip()
    if not domain_input:
        return JsonResponse({'matched': False})

    # Normalize: strip protocol, extract hostname from URL if needed
    if '://' in domain_input:
        parsed = urlparse(domain_input)
        normalized = parsed.hostname or domain_input
    else:
        normalized = domain_input
    # Remove port if present
    if ':' in normalized:
        normalized = normalized.split(':')[0]
    normalized = normalized.strip().lower()

    if not normalized:
        return JsonResponse({'matched': False})

    base_dir = _get_base_dir()
    from site_adapters.services.config.loader import load_domain_config
    url = f'https://{normalized}'
    config = load_domain_config(url, base_dir)
    if config:
        domain_key = config.get('_domain_key', normalized)
        auth_req = get_auth_requirements_for_domain_key(domain_key, base_dir=base_dir)
        return JsonResponse({
            'matched': True,
            'domain_key': domain_key,
            'auth_requirements': auth_req,
        })
    return JsonResponse({'matched': False})

"""OpenAPI 3.x / Swagger 2: đọc spec (JSON, YAML), giải $ref nội bộ, liệt kê endpoint, sinh body mẫu và dựng request."""
import copy
import json
import re
from urllib.parse import quote

from .httpclient import new_request

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

HTTP_METHODS = ('get', 'put', 'post', 'delete', 'patch', 'head', 'options')
MAX_DEPTH = 6


class SpecError(ValueError):
    pass


def load_spec(text):
    """JSON hoặc YAML → dict spec. Ném SpecError nếu không phải spec hợp lệ."""
    spec = None
    try:
        spec = json.loads(text)
    except ValueError:
        if yaml is not None:
            try:
                spec = yaml.safe_load(text)
            except yaml.YAMLError as e:
                raise SpecError(f'Không đọc được file (JSON/YAML): {e}') from None
        else:
            raise SpecError('Không phải JSON hợp lệ (chưa có PyYAML để đọc YAML)') from None
    if not isinstance(spec, dict) or not isinstance(spec.get('paths'), dict) or not (spec.get('openapi') or spec.get('swagger')):
        raise SpecError('Đây không phải tài liệu OpenAPI/Swagger (thiếu openapi/swagger hoặc paths)')
    return spec


def deref(spec, node, stack=()):
    """Giải $ref nội bộ (#/a/b). Trả (node, stack mới). Ref lặp vòng → ({}, stack)."""
    while isinstance(node, dict) and '$ref' in node:
        ref = node['$ref']
        if not isinstance(ref, str) or not ref.startswith('#/'):
            return {}, stack
        if ref in stack:
            return {}, stack
        stack = stack + (ref,)
        cur = spec
        for part in ref[2:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            if not isinstance(cur, dict) or part not in cur:
                return {}, stack
            cur = cur[part]
        node = cur
    return node, stack


def merge_all_of(spec, schema, stack=()):
    """Gộp allOf thành một schema duy nhất (properties/required được hợp nhất)."""
    schema, stack = deref(spec, schema, stack)
    if not isinstance(schema, dict):
        return {}
    out = {k: v for k, v in schema.items() if k != 'allOf'}
    for sub in schema.get('allOf') or []:
        m = merge_all_of(spec, sub, stack)
        for k, v in m.items():
            if k == 'properties':
                out.setdefault('properties', {}).update(v)
            elif k == 'required':
                out['required'] = sorted(set(out.get('required', [])) | set(v))
            else:
                out.setdefault(k, v)
    return out


FORMATS = {'date-time': '2024-01-01T00:00:00Z', 'date': '2024-01-01', 'time': '00:00:00', 'email': 'user@example.com', 'uuid': '00000000-0000-0000-0000-000000000000',
           'uri': 'https://example.com', 'url': 'https://example.com', 'hostname': 'example.com', 'ipv4': '127.0.0.1', 'password': 'string', 'byte': 'c3RyaW5n'}


def sample(spec, schema, depth=0, stack=()):
    """Giá trị mẫu cho một schema (ưu tiên example/default/enum)."""
    schema = merge_all_of(spec, schema, stack)
    if not schema:
        return None
    for key in ('example', 'default', 'const'):
        if key in schema:
            return copy.deepcopy(schema[key])
    if schema.get('enum'):
        return schema['enum'][0]
    for key in ('oneOf', 'anyOf'):
        if schema.get(key):
            return sample(spec, schema[key][0], depth + 1, stack)
    t = schema.get('type')
    if isinstance(t, list):
        t = next((x for x in t if x != 'null'), 'null')
    if t is None:
        t = 'object' if 'properties' in schema else ('array' if 'items' in schema else None)
    if depth >= MAX_DEPTH:
        return {} if t == 'object' else ([] if t == 'array' else None)
    if t == 'object':
        props = schema.get('properties') or {}
        req = schema.get('required') or []
        order = [k for k in props if k in req] + [k for k in props if k not in req]
        return {k: sample(spec, props[k], depth + 1, stack) for k in order}
    if t == 'array':
        item = sample(spec, schema.get('items') or {}, depth + 1, stack)
        return [] if item is None and not schema.get('items') else [item]
    if t == 'string':
        return FORMATS.get(schema.get('format'), 'string')
    if t == 'integer':
        return int(schema.get('minimum', 0))
    if t == 'number':
        return float(schema.get('minimum', 0))
    if t == 'boolean':
        return False
    return None


def _params(spec, *lists):
    out, seen = [], set()
    for lst in lists:  # path-level trước, operation-level ghi đè
        for p in lst or []:
            p, _ = deref(spec, p)
            if isinstance(p, dict) and p.get('name'):
                key = (p['name'], p.get('in'))
                if key in seen:
                    out = [x for x in out if (x['name'], x.get('in')) != key]
                seen.add(key)
                out.append(p)
    return out


def endpoints(spec):
    """[{'method','path','tag','summary','params','body_schema','content_type'}] sắp theo tag rồi path."""
    out = []
    swagger2 = bool(spec.get('swagger'))
    for path, item in (spec.get('paths') or {}).items():
        item, _ = deref(spec, item)
        if not isinstance(item, dict):
            continue
        for m in HTTP_METHODS:
            op = item.get(m)
            if not isinstance(op, dict):
                continue
            params = _params(spec, item.get('parameters'), op.get('parameters'))
            body_schema, ctype = None, ''
            if swagger2:
                bp = next((p for p in params if p.get('in') == 'body'), None)
                if bp:
                    body_schema, ctype = bp.get('schema') or {}, (op.get('consumes') or spec.get('consumes') or ['application/json'])[0]
                form = [p for p in params if p.get('in') == 'formData']
                if form and not bp:
                    body_schema, ctype = {'type': 'object', 'properties': {p['name']: p for p in form}}, 'application/x-www-form-urlencoded'
                params = [p for p in params if p.get('in') in ('path', 'query', 'header')]
            else:
                rb, _ = deref(spec, op.get('requestBody') or {})
                content = (rb or {}).get('content') or {}
                for c in ('application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'):
                    match = next((k for k in content if k == c or k.endswith('+json') and c == 'application/json'), None)
                    if match:
                        ctype, body_schema = match, (content[match] or {}).get('schema') or {}
                        break
                else:
                    if content:
                        ctype, body_schema = next(iter(content)), (next(iter(content.values())) or {}).get('schema') or {}
            tags = op.get('tags') or ['(không có tag)']
            out.append({'method': m.upper(), 'path': path, 'tag': tags[0], 'summary': op.get('summary') or op.get('operationId') or '', 'params': params,
                        'body_schema': body_schema, 'content_type': ctype, 'deprecated': bool(op.get('deprecated'))})
    return sorted(out, key=lambda e: (e['tag'].lower(), e['path'], HTTP_METHODS.index(e['method'].lower())))


def servers(spec):
    """URL gốc gợi ý từ spec."""
    if spec.get('servers'):
        return [s.get('url', '') for s in spec['servers'] if isinstance(s, dict) and s.get('url')]
    if spec.get('swagger') and spec.get('host'):
        scheme = (spec.get('schemes') or ['http'])[0]
        return [f"{scheme}://{spec['host']}{spec.get('basePath', '')}"]
    return []


def param_value(spec, p):
    s = p.get('schema') or p
    v = p.get('example', None)
    if v is None:
        v = sample(spec, s)
    return v


def request_from(spec, ep, base='{{base_url}}'):
    """Endpoint → request lưu được: path param đã điền mẫu, query param (bắt buộc bật sẵn), header, body mẫu."""
    path = ep['path']
    for p in ep['params']:
        if p.get('in') == 'path':
            val = param_value(spec, p)
            path = path.replace('{' + p['name'] + '}', quote('' if val is None else str(val), safe=''))
    path = re.sub(r'\{[^}]+\}', '', path)  # path param không khai báo
    req = new_request(name=f"{ep['method']} {ep['path']}", method=ep['method'], url=base.rstrip('/') + path)
    for p in ep['params']:
        val = param_value(spec, p)
        row = {'on': bool(p.get('required')), 'k': p['name'], 'v': '' if val is None else (json.dumps(val) if isinstance(val, (dict, list)) else str(val))}
        if p.get('in') == 'query':
            req['params'].append(row)
        elif p.get('in') == 'header':
            req['headers'].append(row)
    if ep['body_schema'] is not None and ep['method'] not in ('GET', 'HEAD'):
        ct = ep['content_type']
        body = sample(spec, ep['body_schema'])
        if 'json' in ct or not ct:
            req['body_type'], req['body'] = 'json', json.dumps(body, ensure_ascii=False, indent=2)
        elif 'x-www-form-urlencoded' in ct or 'multipart' in ct:
            req['body_type'] = 'form'
            if isinstance(body, dict):
                req['form'] = [{'on': True, 'k': k, 'v': '' if v is None else str(v)} for k, v in body.items()]
        else:
            req['body_type'], req['body'] = 'raw', json.dumps(body) if not isinstance(body, str) else body
            req['headers'].append({'on': True, 'k': 'Content-Type', 'v': ct})
    return req

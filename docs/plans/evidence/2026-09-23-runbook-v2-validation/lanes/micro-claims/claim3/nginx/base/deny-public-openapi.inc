# CDL WASA #12 — do not publish the gateway OpenAPI/Scalar surface on the
# public vhost. Control-plane /api/schema/ is already admin-gated. Authenticated
# operators can still fetch the spec on the internal docker network (:8300)
# after presenting a gateway API key (middleware no longer excludes these paths).
location = /openapi.json {
    include /etc/nginx/security-headers.inc;
    default_type application/json;
    add_header WWW-Authenticate 'Bearer realm="gateway"' always;
    return 401 '{"error":{"message":"Authentication required","type":"authentication_error","code":"unauthorized"}}';
}
location = /docs {
    include /etc/nginx/security-headers.inc;
    default_type application/json;
    add_header WWW-Authenticate 'Bearer realm="gateway"' always;
    return 401 '{"error":{"message":"Authentication required","type":"authentication_error","code":"unauthorized"}}';
}
location = /docs/ {
    include /etc/nginx/security-headers.inc;
    default_type application/json;
    add_header WWW-Authenticate 'Bearer realm="gateway"' always;
    return 401 '{"error":{"message":"Authentication required","type":"authentication_error","code":"unauthorized"}}';
}
location = /redoc {
    include /etc/nginx/security-headers.inc;
    default_type application/json;
    add_header WWW-Authenticate 'Bearer realm="gateway"' always;
    return 401 '{"error":{"message":"Authentication required","type":"authentication_error","code":"unauthorized"}}';
}

# Control-plane internal endpoints are for gateways on the docker network only
# (they dial http://control:8000 directly). Never serve them on a public vhost:
# /api/mcp-connector/internal/credential/ returns decrypted upstream credentials.
location ^~ /api/mcp-connector/internal/ {
    include /etc/nginx/security-headers.inc;
    default_type application/json;
    return 404 '{"error":{"message":"Not found","type":"invalid_request_error","code":"not_found"}}';
}

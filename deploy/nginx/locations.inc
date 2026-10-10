# Shared by http.conf and https.conf. Upstreams are re-resolved (Docker DNS) so recreated
# api/web containers with new IPs are found without restarting the proxy.
resolver 127.0.0.11 valid=10s ipv6=off;
set $api http://api:8000;
set $web http://web:3000;

client_max_body_size 1m;

# only the client's own address: the API trusts no forwarded header, but logs stay honest
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;

# login: per-IP rate limit, so one host cannot lock everyone's account (5 failures = 10 min)
location = /api/auth/login {
    limit_req zone=login burst=5 nodelay;
    limit_req_status 429;
    include /etc/nginx/axcad/headers.inc;
    add_header Cache-Control "no-store" always;
    proxy_pass $api;
}

location = /api { return 404; }

location /api/ {
    # STEP/IGES imports are capped at 100 MB by the API (DXF 50 MB); the API rejects oversize itself
    client_max_body_size 101m;
    proxy_read_timeout 240s;  # import slot wait (60 s) + exchange (120 s) + margin
    proxy_send_timeout 240s;
    include /etc/nginx/axcad/headers.inc;
    add_header Cache-Control "no-store" always;
    proxy_pass $api;
}

location / {
    include /etc/nginx/axcad/headers.inc;
    proxy_pass $web;
}

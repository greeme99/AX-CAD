# Repeated per location: nginx drops server-level add_header once a location adds its own.
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "no-referrer" always;
# HTTPS only in effect: browsers ignore HSTS received over plain HTTP
add_header Strict-Transport-Security "max-age=31536000" always;

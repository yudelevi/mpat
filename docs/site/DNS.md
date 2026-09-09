# DNS and Pages setup

Not part of the built site. Operator notes for `monkeypat.ch`.

## Prerequisite

GitHub Pages on a private repository requires GitHub Pro. Make `yudelevi/mpat`
public, or upgrade the plan, before any of the below will serve.

## Cloudflare records

Set every record to DNS only (grey cloud) until GitHub has issued the Pages
certificate. Proxying blocks the ACME validation.

Apex `monkeypat.ch`, four A records:

```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

Apex `monkeypat.ch`, four AAAA records:

```
2606:50c0:8000::153
2606:50c0:8001::153
2606:50c0:8002::153
2606:50c0:8003::153
```

`www`, one CNAME:

```
yudelevi.github.io
```

If you turn the orange cloud on later, set SSL/TLS mode to Full. Flexible would
downgrade the GitHub leg to plain HTTP and cause a redirect loop once Enforce
HTTPS is on.

## GitHub side

1. Settings, Pages, Source: GitHub Actions.
2. Settings, Pages, Custom domain: `monkeypat.ch`. Save and wait for the DNS
   check to pass.
3. Tick Enforce HTTPS once the certificate is issued.
4. Verify the domain under account Settings, Pages, so nobody else can claim a
   subdomain of it.

`docs/site/pages/CNAME` is copied into the build output by mkdocs, so the custom
domain survives every deploy. Do not delete it.
